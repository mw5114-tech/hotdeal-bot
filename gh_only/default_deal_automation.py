"""Read-only @jirum deal enrichment: Telegram price + Lowchart exact-item photo and history.

CLI intentionally processes at most one recent Coupang post per execution (no cron,
no retries, no publishing, no affiliate rewriting). Default production photo
source is public Lowchart, never a Kakao login; legacy Kakao code is diagnostic only.
Product/option mismatches suppress historical discount claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib import request, error
from urllib.parse import parse_qs, urlsplit

from gh_only.pipeline import enrich_posts, fetch_public_html, identify_url
from gh_only.daangn_draft import normalized_title
from gh_only.shortlink_probe import one_hop, StopRedirect
from gh_only.lowchart_price_reference import fetch_one, build_url
from gh_only.default_deal_format import (
    create_default_preview, validated_history, normalized_product_name,
)
from gh_only.affsdp_candidate import compose_affsdp_candidate
from gh_only.lowchart_product_photo import (
    lookup_exact_item_photo, download_exact_photo, is_coupang_cdn_url,
)
from gh_only.telegram_source_photo import download_source_photo
from gh_only.search_product_photo import (
    lookup_search_product_photo, download_search_photo,
)
ALLOWED_CDN = ("thumbnail.coupangcdn.com", "image.coupangcdn.com")
MAX_IMAGE_BYTES = 2_500_000


def resolve_identity(link: str) -> dict:
    """One allowed shortlink redirect; no Coupang product HTML fetch."""
    identity = identify_url(link)
    if identity.get("url_kind") == "coupang_shortlink":
        try:
            result = one_hop(link)
        except Exception as exc:
            return {"status": "SHORTLINK_ERROR", "error_type": type(exc).__name__}
        if result.get("result") != "DIRECT_PRODUCT_ID_CANDIDATE":
            return {"status": result.get("result", "UNRESOLVED")}
        identity = {
            "url_kind": "coupang_shortlink",
            "product_id": result.get("product_id"),
            "item_id": result.get("item_id"),
            "vendor_item_id": result.get("vendor_item_id"),
        }
    if identity.get("url_kind") not in ("coupang_shortlink", "coupang_product"):
        return {"status": "NOT_COUPANG_PRODUCT"}
    p, i = identity.get("product_id"), identity.get("item_id")
    if not (isinstance(p, str) and p.isdigit()
            and isinstance(i, str) and i.isdigit()):
        return {"status": "EXACT_ITEM_ID_MISSING", **identity}
    return {"status": "EXACT_ITEM_IDENTIFIED", **identity}


def get_kakao_og(link: str, secret: str) -> dict:
    """Single user-facing Kakao sharing OG lookup; no session file output."""
    out = {"status": "NOT_RUN", "submitted_queries": 0,
           "og_title": None, "og_url": None, "og_image": None}
    if not secret:
        out["status"] = "KAKAO_SESSION_SECRET_MISSING"
        return out
    from gh_only.kakao_og_authenticated_probe import (TOOL_URL, extract_og_table, safe_session_state)
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        state = safe_session_state(secret)
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(storage_state=state, locale="ko-KR")
                page = ctx.new_page()
                page.goto(TOOL_URL, wait_until="domcontentloaded", timeout=25000)
                p = urlsplit(page.url)
                if p.hostname == "accounts.kakao.com" or "/login" in p.path:
                    out["status"] = "KAKAO_LOGIN_EXPIRED"
                    return out
                if p.hostname != "developers.kakao.com":
                    out["status"] = "UNEXPECTED_PAGE"
                    return out
                try:
                    page.wait_for_load_state("networkidle", timeout=9000)
                except PWTimeout:
                    pass
                field = None
                for css in ('input[placeholder*="URL"]', 'input[aria-label*="URL"]',
                            'input[type="url"]', 'input[placeholder*="url"]'):
                    el = page.locator(css)
                    if el.count() and el.first.is_visible():
                        field = el.first
                        break
                if field is None:
                    out["status"] = "KAKAO_URL_FIELD_NOT_FOUND"
                    return out
                field.fill(link, timeout=6500)
                button = page.get_by_role("button", name=re.compile("메타 정보 조회"))
                if not button.count():
                    button = page.get_by_text("메타 정보 조회", exact=True)
                if not button.count():
                    out["status"] = "KAKAO_BUTTON_NOT_FOUND"
                    return out
                button.first.click(timeout=7000)
                out["submitted_queries"] = 1
                try:
                    page.get_by_text("og:image", exact=True).wait_for(timeout=18000)
                except PWTimeout:
                    pass
                md = extract_og_table(page)
                out.update({"status": "KAKAO_METADATA_READ" if md else "KAKAO_NO_METADATA",
                            "og_title": md.get("og:title"),
                            "og_url": md.get("og:url"),
                            "og_image": md.get("og:image")})
            finally:
                browser.close()
    except Exception as exc:
        out["status"] = "KAKAO_EXCEPTION"
        out["error_type"] = type(exc).__name__
    return out


def og_matches_product(og: dict, identity: dict) -> bool:
    page_url = og.get("og_url")
    if not isinstance(page_url, str):
        return False
    try:
        u = urlsplit(page_url)
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password:
        return False
    if u.hostname in ("lowchart.com", "www.lowchart.com"):
        return (og.get("status") == "LOWCHART_EXACT_ITEM_IMAGE"
                and u.path.rstrip("/") ==
                f"/{identity.get('product_id')}-{identity.get('item_id')}"
                and not u.query)
    if (u.hostname not in ("coupang.com", "www.coupang.com")
            or u.path.rstrip("/") != f"/vp/products/{identity.get('product_id')}"):
        return False
    vendor = identity.get("vendor_item_id")
    og_vendor = parse_qs(u.query).get("vendorItemId", [])
    return not (vendor and og_vendor and (len(og_vendor) != 1 or og_vendor[0] != vendor))


def get_title_tokens(title: str | None) -> tuple[str, ...]:
    if not title:
        return ()
    base = re.split(r"\s+[-–]\s+", title, maxsplit=1)[0].strip()
    words = [w.strip(" ,()[]") for w in base.split() if len(w.strip(" ,()[]")) >= 2]
    weights = re.findall(r"\b\d+(?:\.\d+)?\s*(?:kg|g|ml|l|개|팩|입|봉|병|정)\b",
                         base, flags=re.I)
    # Brand + last one or two package quantities; strict title equivalence
    # also checked independently by validated_history.
    chosen = ([words[0]] if words else []) + [x.replace(" ", "") for x in weights[-2:]]
    return tuple(dict.fromkeys(chosen))


def download_og_photo(url: str | None, output: Path, source_message_id: int) -> dict:
    out = {"status": "NOT_DOWNLOADED", "saved_name": None,
           "bytes": 0, "width_height_verified": False}
    from gh_only.kakao_og_authenticated_probe import image_is_cdn
    if not isinstance(url, str) or not image_is_cdn(url):
        out["status"] = "NO_TRUSTED_CDN_URL"
        return out
    u = urlsplit(url)
    if u.hostname not in ALLOWED_CDN or u.username or u.password:
        out["status"] = "IMAGE_HOST_REJECTED"
        return out
    req = request.Request(url, headers={"Accept": "image/*"})
    opener = request.build_opener(StopRedirect)
    try:
        with opener.open(req, timeout=25) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
            mime = resp.headers.get_content_type()
            status = resp.status
    except Exception as exc:
        out["status"] = "IMAGE_FETCH_ERROR"
        out["error_type"] = type(exc).__name__
        return out
    ext = { "image/jpeg": ".jpg", "image/png": ".png",
            "image/webp": ".webp" }.get(mime)
    valid = ((ext == ".jpg" and data.startswith(b"\xff\xd8\xff"))
             or (ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
             or (ext == ".webp" and data.startswith(b"RIFF") and data[8:12] == b"WEBP"))
    if status != 200 or not valid or not (1000 <= len(data) <= MAX_IMAGE_BYTES):
        out["status"] = "IMAGE_INVALID"
        return out
    filename = f"jirum_{int(source_message_id)}_kakao_og{ext}"
    (output / filename).write_bytes(data)
    out.update({"status": "IMAGE_SAVED", "saved_name": filename, "bytes": len(data)})
    return out


def enrich_deal(deal: dict, output_dir: Path,
                resolver=resolve_identity, og_lookup=None,
                history_fetch=fetch_one, photo_downloader=None,
                session_secret: str = "", owner_tag: str = "",
                *, search_lookup=None, search_downloader=None,
                naver_price_lookup=None, fallcent_lookup=None,
                fallcent_high_lookup=None) -> dict:
    """Dependency-injected single item for deterministic offline contract tests."""
    source_id = deal.get("source_message_id")
    sale = deal.get("sale_price_krw")
    link = deal.get("original_purchase_url") or ""
    fallback_title = normalized_title(deal.get("source_text") or "") or deal.get("title")
    identity = resolver(link)
    p, i = identity.get("product_id"), identity.get("item_id")
    # Explicit injectable overrides retain existing read-only regression
    # contracts; production never calls the login-based Kakao debugger.
    using_public_lowchart = og_lookup is None
    if using_public_lowchart:
        og_lookup = lambda _link, _session: lookup_exact_item_photo(identity, fallback_title)
        photo_downloader = photo_downloader or download_exact_photo
    else:
        photo_downloader = photo_downloader or download_og_photo
    og = (og_lookup(link, session_secret)
          if identity.get("status") == "EXACT_ITEM_IDENTIFIED"
          else {"status": "UNRESOLVED_COUPANG_ITEM", "og_title": None,
                "og_url": None, "og_image": None})
    og_title = og.get("og_title") if isinstance(og.get("og_title"), str) else None
    # Safety: if OG explicitly contradicts link product/vendor, do not use its
    # image or price-title identity. Still keep the source sale.
    matching = (identity.get("status") == "EXACT_ITEM_IDENTIFIED"
                and og_matches_product(og, identity))
    # The name displayed in BOTH Daangn headline and body comes from the
    # verified exact-item Lowchart page. Telegram is retained for diagnostics
    # only when the page is unavailable; the draft then cannot be published.
    lowchart_title_verified = bool(
        using_public_lowchart
        and identity.get("status") == "EXACT_ITEM_IDENTIFIED"
        and og.get("lowchart_title_verified") is True
        and isinstance(og_title, str) and bool(og_title.strip())
        and og.get("og_url") == build_url(p, i)
    )
    title = (
        og_title.strip() if lowchart_title_verified
        else fallback_title if using_public_lowchart
        else (re.split(r"\s+[-–]\s+", og_title, maxsplit=1)[0]
              if matching and og_title else fallback_title)
    )
    history = {"reason": "LINK_OR_OG_NOT_VERIFIED", "usable_historical_reference": False}
    average = None
    if matching and isinstance(sale, int) and sale > 0:
        try:
            history = history_fetch(p, i, get_title_tokens(og_title), sale)
            average = validated_history(history, p, i, og_title)
        except Exception as exc:
            history = {"reason": "HISTORY_QUERY_ERROR",
                       "error_type": type(exc).__name__}

    # Lowchart historical average is preferred when available. Missing history
    # may be replaced with a *confirmed* Fallcent observed high only when the
    # Fallcent photo was selected, otherwise existing Naver comparison fallback.
    comparison_price = average
    comparison_source = "lowchart_public_history"
    naver_price = {"status": "NOT_ATTEMPTED", "price_krw": None}
    fallcent_high = {"status": "NOT_ATTEMPTED", "observed_high_krw": None}
    image_source = og.get(
        "image_source",
        "lowchart_exact_item_public_page" if using_public_lowchart
        else "legacy_kakao_og",
    )
    photo = {"status": "NO_MATCHING_PRODUCT_IMAGE", "saved_name": None}
    if not matching:
        eligible_cdn = False
    elif image_source == "lowchart_exact_item_public_page":
        eligible_cdn = is_coupang_cdn_url(og.get("og_image"))
    else:
        from gh_only.kakao_og_authenticated_probe import image_is_cdn
        eligible_cdn = image_is_cdn(og.get("og_image") or "")
    if matching and eligible_cdn:
        photo = photo_downloader(og["og_image"], output_dir, source_id)
    # Photo order: Lowchart exact item -> Fallcent search -> Naver search.
    # Search results are representative images, not exact item/option proof.
    source_photo_verified = False
    search_result = {"status": "NOT_ATTEMPTED"}
    search_photo_verified = False
    search_title = None
    provider_photo = {}
    photo_attempts = []
    if (using_public_lowchart
            and identity.get("status") == "EXACT_ITEM_IDENTIFIED"
            and photo.get("status") != "IMAGE_SAVED"):
        from gh_only.fallcent_search_photo import fetch_fallcent_product_photo
        fetch_fallcent = fallcent_lookup or fetch_fallcent_product_photo
        try:
            provider_photo = fetch_fallcent(
                fallback_title or title, output_dir, source_id)
        except Exception as exc:
            provider_photo = {"status": "FALLCENT_LOOKUP_ERROR",
                              "error_type": type(exc).__name__}
        photo_attempts.append({
            "provider": "fallcent_search",
            "status": provider_photo.get("status"),
            "error_type": provider_photo.get("error_type"),
        })
        if (provider_photo.get("status") != "IMAGE_SAVED"
                or provider_photo.get("image_source") != "fallcent_search_representative"
                or provider_photo.get("representative_fallcent_image") is not True):
            from gh_only.naver_web_image_photo import fetch_first_naver_photo
            try:
                provider_photo = fetch_first_naver_photo(
                    fallback_title or title, output_dir, source_id)
            except Exception as exc:
                provider_photo = {"status": "NAVER_IMAGE_LOOKUP_ERROR",
                                  "error_type": type(exc).__name__}
            photo_attempts.append({
                "provider": "naver_web",
                "status": provider_photo.get("status"),
                "error_type": provider_photo.get("error_type"),
            })
        if provider_photo.get("status") == "IMAGE_SAVED":
            photo = {k: provider_photo[k] for k in ("status", "saved_name",
                     "bytes", "image_source") if k in provider_photo}
            image_source = provider_photo["image_source"]
            title = fallback_title or title
        else:
            photo = {"status": "PHOTO_EVIDENCE_UNAVAILABLE", "saved_name": None}
    provider_verified = False
    representative_fallcent_image = (
        provider_photo.get("status") == "IMAGE_SAVED"
        and provider_photo.get("image_source") == "fallcent_search_representative"
        and provider_photo.get("representative_fallcent_image") is True
        and provider_photo.get("image_match_basis") ==
            "UNVERIFIED_FALLCENT_SEARCH_RESULT"
    )
    representative_naver_image = (
        provider_photo.get("status") == "IMAGE_SAVED"
        and provider_photo.get("image_source") == "naver_web_first_image"
        and provider_photo.get("image_match_basis") ==
            "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT"
    )
    # Only an actual downloaded Fallcent representative photo enables
    # Fallcent historical-high lookup. It is a historical observed MAXIMUM,
    # never an average or Coupang's ordinary/list price.
    if (using_public_lowchart and average is None
            and representative_fallcent_image
            and type(sale) is int and 0 < sale <= 100_000_000):
        try:
            if fallcent_high_lookup is None:
                from gh_only.fallcent_high_reference import fetch_exact_fallcent_high
                fallcent_high_lookup = fetch_exact_fallcent_high
            fallcent_high = fallcent_high_lookup(identity, fallback_title or title)
            high = fallcent_high.get("observed_high_krw")
            if (fallcent_high.get("status") == "HIGH_FOUND"
                    and type(high) is int and sale < high <= 100_000_000
                    and fallcent_high.get("product_id") == p
                    and fallcent_high.get("item_id") == i
                    and fallcent_high.get("price_basis") ==
                        "fallcent_public_visible_historical_high"):
                comparison_price = high
                comparison_source = "fallcent_observed_high"
        except Exception as exc:
            fallcent_high = {"status": "FALLCENT_HIGH_QUERY_ERROR",
                             "observed_high_krw": None,
                             "error_type": type(exc).__name__}

    # Naver is third priority for a comparison price, never treated as
    # a historical high or an exact-variant reference.
    if (using_public_lowchart
            and identity.get("status") == "EXACT_ITEM_IDENTIFIED"
            and comparison_price is None and type(sale) is int and sale > 0
            and os.getenv("JIRUM_ENABLE_NAVER_PRICE_FALLBACK") == "true"):
        try:
            if naver_price_lookup is None:
                from gh_only.naver_web_product_price import fetch_first_naver_product_price
                naver_price_lookup = fetch_first_naver_product_price
            naver_price = naver_price_lookup(fallback_title or title)
            quote = naver_price.get("price_krw")
            if (naver_price.get("status") == "PRICE_FOUND"
                    and type(quote) is int and 100 <= quote <= 100_000_000):
                comparison_price = quote
                comparison_source = "naver_general_search_first_shopping_card"
        except Exception as exc:
            naver_price = {"status": "NAVER_PRICE_QUERY_ERROR",
                           "price_krw": None, "error_type": type(exc).__name__}
    caption = create_default_preview(
        sale, title, comparison_price, source_id,
        reference_source=comparison_source)
    # The candidate is composed ONLY from the already-resolved product/item.
    # It has not been formally issued and its commission credit is unknown.
    candidate = (
        compose_affsdp_candidate(
            identity, owner_tag, subid=os.getenv("COUPANG_PARTNER_SUBID", "")
        ) if owner_tag else
        {"status": "OWNER_LPTAG_NOT_CONFIGURED", "candidate_url": None,
         "official_link_issued": False, "commission_attribution_verified": False,
         "ready_for_automatic_publication": False}
    )
    if candidate.get("candidate_url"):
        caption["caption"] += (
            "\n\n🛒 쿠팡에서 상품정보 더보기 : " + candidate["candidate_url"]
        )
    return {
        "source_post_url": deal.get("source_post_url"),
        "source_message_id": source_id,
        "status": "DRAFT_ONLY_DO_NOT_PUBLISH",
        "caption": caption,
        "product_identity": identity,
        "og_title": og_title,
        "og_url": og.get("og_url"),
        "lowchart_title_verified": lowchart_title_verified,
        "lowchart_product_title": og_title.strip() if lowchart_title_verified else None,
        "product_name_source": (
            "lowchart_exact_item_page" if lowchart_title_verified
            else "telegram_product_title" if (
                representative_naver_image or representative_fallcent_image)
            else "unverified_fallback"
        ) if using_public_lowchart else "legacy_kakao",
        "search_product_title": search_title,
        "image_search_product_id_verified": search_photo_verified,
        "image_search_title_verified": search_photo_verified,
        "image_search_exact_item_id_verified": False,
        "search_status": (provider_photo.get("status") if (
            representative_naver_image or representative_fallcent_image)
            else search_result.get("status")),
        "search_provider": ("naver_image_index" if search_photo_verified else None),
        "kakao_status": og.get("status"),
        "kakao_product_match": matching and image_source == "legacy_kakao_og",
        "image_product_match": matching or provider_verified,
        "image_source": image_source,
        "image_metadata_status": og.get("status"),
        "image_http_status": og.get("http_status"),
        "image_error_type": og.get("error_type"),
        "source_photo_verified": source_photo_verified,
        "image_match_basis": (
            "UNVERIFIED_FALLCENT_SEARCH_RESULT" if representative_fallcent_image
            else "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT" if representative_naver_image
            else og.get("image_match_basis")
        ),
        "image_same_named_option": (
            provider_photo.get("image_provider_item_id") == i if provider_verified
            else og.get("image_same_named_option")
        ),
        "image_provider_verified": provider_verified,
        "representative_naver_image": representative_naver_image,
        "representative_fallcent_image": representative_fallcent_image,
        "image_provider_title": provider_photo.get("image_provider_title"),
        "image_provider_product_id": provider_photo.get("image_provider_product_id"),
        "image_provider_item_id": provider_photo.get("image_provider_item_id"),
        "photo_provider_attempts": photo_attempts,
        "photo": photo,
        "history": history,
        "history_avg_accepted": average is not None,
        "naver_price_lookup": naver_price,
        "fallcent_high_lookup": fallcent_high,
        "comparison_price_source": (comparison_source if comparison_price is not None else None),
        "observed_low_verified_for_deal": (
            average is not None
            and type(history.get("observed_low_krw")) is int
            and type(sale) is int
            and sale <= history["observed_low_krw"]
        ),
        "image_exact_option_verified": False,
        "image_exact_item_page_verified": bool(
            matching and image_source == "lowchart_exact_item_public_page"),
        "price_is_live_coupang_verified": False,
        "posts_published": 0,
        "personal_affiliate_link_created": False,
        "affsdp": candidate,
        "composed_affiliate_url": candidate.get("candidate_url"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="default-deal-drafts")
    parser.add_argument("--max-source-posts", type=int, default=8)
    parser.add_argument("--exclude-source-id", type=int, action="append", default=[])
    parser.add_argument("--source-id", type=int, default=None)
    args = parser.parse_args()
    if not 1 <= args.max_source_posts <= 20:
        parser.error("max-source-posts must be 1..20")
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    evidence = enrich_posts(fetch_public_html(), max_posts=args.max_source_posts)
    # Latest viable Coupang post only; explicit one metadata lookup per run.
    eligible = [d for d in reversed(evidence)
                if d.get("url_kind") in ("coupang_shortlink", "coupang_product")
                and isinstance(d.get("sale_price_krw"), int)
                and d["sale_price_krw"] > 0]
    selected = next(
        (d for d in eligible
         if d.get("source_message_id") not in args.exclude_source_id
         and (args.source_id is None or d.get("source_message_id") == args.source_id)),
        None
    )
    results = []
    if selected is not None:
        results.append(enrich_deal(selected, root,
                                  session_secret=os.getenv("KAKAO_DEV_STORAGE_STATE_JSON", ""),
                                  owner_tag=os.getenv("COUPANG_PARTNER_LPTAG", "")))
    report = {
        "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://t.me/s/jirum",
        "source_posts_sampled": len(evidence),
        "eligible_coupang_posts": len(eligible),
        "attempted_items": len(results),
        "max_metadata_lookups": 1,
        "publish_count": 0,
        "affiliate_link_issuance_count": 0,
        "affiliate_commission_verified": False,
        "drafts": results,
    }
    (root / "drafts.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "captions.txt").write_text(
        "\n\n".join(r["caption"]["caption"] for r in results) +
        ("\n" if results else ""), encoding="utf-8")
    safe = [{
        "message_id": r["source_message_id"],
        "sale": r["caption"]["sale_price_krw"],
        "observed_average": r["caption"]["observed_average_krw"],
        "approx_discount": r["caption"]["approx_discount_vs_historical_mean_pct"],
        "history_reason": r["history"].get("reason"),
        "og_status": r["kakao_status"],
        "photo_status": r["photo"]["status"],
        "image_file": r["photo"].get("saved_name"),
        "affsdp_status": r["affsdp"]["status"],
        "has_composed_url": bool(r["composed_affiliate_url"])
    } for r in results]
    print("JIRUM_DEFAULT_AUTOMATION " + json.dumps(
        {"source_posts": len(evidence), "eligible": len(eligible),
         "drafts": safe, "published": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
