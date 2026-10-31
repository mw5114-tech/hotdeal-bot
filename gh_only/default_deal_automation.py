"""Read-only default @jirum deal enrichment: Telegram price + Kakao OG photo + Lowchart average.

CLI intentionally processes at most one recent Coupang post per execution (no cron,
no retries, no publishing, no affiliate rewriting). Uses user-authorized Kakao
Developers session only for the user-facing metadata debugger, never logs cookies.
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
from gh_only.lowchart_price_reference import fetch_one
from gh_only.kakao_og_authenticated_probe import (
    TOOL_URL, extract_og_table, image_is_cdn, safe_session_state
)
from gh_only.default_deal_format import (
    create_default_preview, validated_history, normalized_product_name,
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
    if (u.scheme != "https" or u.hostname not in ("coupang.com", "www.coupang.com")
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
                resolver=resolve_identity, og_lookup=get_kakao_og,
                history_fetch=fetch_one, photo_downloader=download_og_photo,
                session_secret: str = "") -> dict:
    """Dependency-injected single item for deterministic offline contract tests."""
    source_id = deal.get("source_message_id")
    sale = deal.get("sale_price_krw")
    link = deal.get("original_purchase_url") or ""
    fallback_title = normalized_title(deal.get("source_text") or "") or deal.get("title")
    identity = resolver(link)
    p, i = identity.get("product_id"), identity.get("item_id")
    og = og_lookup(link, session_secret)
    og_title = og.get("og_title") if isinstance(og.get("og_title"), str) else None
    # Safety: if OG explicitly contradicts link product/vendor, do not use its
    # image or price-title identity. Still keep the source sale.
    matching = (identity.get("status") == "EXACT_ITEM_IDENTIFIED"
                and og_matches_product(og, identity))
    title = (re.split(r"\s+[-–]\s+", og_title, maxsplit=1)[0] if matching and og_title
             else fallback_title)
    history = {"reason": "LINK_OR_OG_NOT_VERIFIED", "usable_historical_reference": False}
    average = None
    if matching and isinstance(sale, int) and sale > 0:
        try:
            history = history_fetch(p, i, get_title_tokens(og_title), sale)
            average = validated_history(history, p, i, og_title)
        except Exception as exc:
            history = {"reason": "HISTORY_QUERY_ERROR",
                       "error_type": type(exc).__name__}
    photo = {"status": "NO_MATCHING_KAKAO_IMAGE", "saved_name": None}
    if matching and image_is_cdn(og.get("og_image") or ""):
        photo = photo_downloader(og["og_image"], output_dir, source_id)
    caption = create_default_preview(sale, title, average, source_id)
    return {
        "source_post_url": deal.get("source_post_url"),
        "source_message_id": source_id,
        "status": "DRAFT_ONLY_DO_NOT_PUBLISH",
        "caption": caption,
        "product_identity": identity,
        "og_title": og_title,
        "og_url": og.get("og_url"),
        "kakao_status": og.get("status"),
        "kakao_product_match": matching,
        "photo": photo,
        "history": history,
        "history_avg_accepted": average is not None,
        "image_exact_option_verified": False,
        "price_is_live_coupang_verified": False,
        "posts_published": 0,
        "personal_affiliate_link_created": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="default-deal-drafts")
    parser.add_argument("--max-source-posts", type=int, default=8)
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
    results = []
    if eligible:
        results.append(enrich_deal(eligible[0], root, session_secret=
                                  os.getenv("KAKAO_DEV_STORAGE_STATE_JSON", "")))
    report = {
        "schema": "github-jirum-default-price-photo-v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://t.me/s/jirum",
        "source_posts_sampled": len(evidence),
        "eligible_coupang_posts": len(eligible),
        "attempted_items": len(results),
        "max_metadata_lookups": 1,
        "publish_count": 0,
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
        "image_file": r["photo"].get("saved_name")
    } for r in results]
    print("JIRUM_DEFAULT_AUTOMATION " + json.dumps(
        {"source_posts": len(evidence), "eligible": len(eligible),
         "drafts": safe, "published": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
