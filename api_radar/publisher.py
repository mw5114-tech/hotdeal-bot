"""A/B-tier Coupang API Radar -> Naver Blog publisher.

Completely independent from Telegram, Daangn and the existing Naver FIFO.
At most 3 verified posts per KST day. A durable claim is written BEFORE any submit;
uncertain failures are quarantined and never auto-retried.

A: exact product/item plus Lowchart-verified >=15% real observed discount.
B: official same-day Goldbox API product without an unverified discount claim.
Both require an exact API product/item URL, valid product image, officially
issued naverblog deeplink, and authenticated SmartEditor session.

No false firsthand reviews; no invented discount/normal price.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

from api_radar.collector import safe_url
from api_radar.quality import (QualityError, resolve_exact_identity,
                               check_exact_history, download_verified_image)
from api_radar.state_store import load_state, save_state, RadarStateError
from api_radar.trend import TrendError, matching_topic, fetch_topic_trend

KST = timezone(timedelta(hours=9))
CANDIDATE_LIMIT = 36
MAX_A_HISTORY_CHECKS_PER_RUN = 12
MAX_PUBLISH_PER_RUN = 2
# Goldbox feeds often contain no exact keyword from optional shopping trends.
# Restrict fallback to official product categories suitable for shopping
# intent; actual search demand remains UNKNOWN without NAVER API HUB.
CURATED_CATEGORY_PRIORITY = (
    "가전디지털", "생활용품", "주방용품", "반려/애완용품",
    "가구/홈인테리어", "뷰티", "식품", "로켓프레시",
)
FRESH_SECONDS = 2 * 3600
MAX_PUBLISH_PER_KST_DAY = 3
WORK_DIR = Path("api-radar-local")
BLOG_PUBLICATION_ENABLED = "API_RADAR_PUBLISH_ENABLED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_stop(reason: str) -> int:
    print("API_RADAR_PUBLISH_SAFE_STOP " + reason + " posts=0")
    return 0


def daily_slot_count(state: dict, now: datetime) -> int:
    """Count confirmed posts AND uncertain attempts against the KST-day cap.

    An ambiguous Naver result must consume a daily slot so the next scheduled
    run cannot exceed the intended number of attempts.
    """
    today = now.astimezone(KST).date().isoformat()
    posted = state.get("published", {})
    claims = state.get("api_publish_claims", {})
    if not isinstance(posted, dict) or not isinstance(claims, dict):
        raise RadarStateError("RADAR_PUBLISH_STATE_INVALID")
    successes = sum(1 for obj in posted.values()
                    if isinstance(obj, dict) and obj.get("kst_date") == today)
    pending = 0
    for obj in claims.values():
        if not isinstance(obj, dict) or not isinstance(obj.get("claimed_at"), str):
            raise RadarStateError("RADAR_CLAIM_DATE_INVALID")
        try:
            when = datetime.fromisoformat(obj["claimed_at"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("timezone missing")
        except (ValueError, TypeError):
            raise RadarStateError("RADAR_CLAIM_DATE_INVALID") from None
        pending += when.astimezone(KST).date().isoformat() == today
    return successes + pending


def select_candidates(state: dict, now: datetime, *, trend_mode="curated") -> list[dict]:
    if state.get("api_publish_claims") and not isinstance(state["api_publish_claims"], dict):
        raise RadarStateError("RADAR_CLAIM_STATE_INVALID")
    posted = state.get("published", {})
    claims = state.get("api_publish_claims", {})
    if not isinstance(posted, dict) or not isinstance(claims, dict):
        raise RadarStateError("RADAR_PUBLISH_STATE_INVALID")
    if daily_slot_count(state, now) >= MAX_PUBLISH_PER_KST_DAY:
        return []
    results = []
    for pid, obj in state.get("products", {}).items():
        if pid in posted or pid in claims or not isinstance(obj, dict):
            continue
        topic = matching_topic(obj.get("name", ""))
        category = obj.get("category")
        fallback = False
        if topic is None and trend_mode == "curated" and category in CURATED_CATEGORY_PRIORITY:
            # Only infer a shopping CATEGORY from the official API. Never
            # present it as measured Naver keyword demand or click volume.
            topic = (category, None)
            fallback = True
        raw_ts = obj.get("last_checked_at_utc")
        if not topic or not isinstance(raw_ts, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            age = (now - timestamp).total_seconds()
        except (ValueError, TypeError):
            continue
        if timestamp.tzinfo is None or not 0 <= age <= FRESH_SECONDS:
            continue
        price = obj.get("last_price")
        url, image = obj.get("last_product_url"), obj.get("last_image_url")
        if (type(price) is not int or not 5000 <= price <= 350000
                or not safe_url(url) or not safe_url(image, image=True)):
            continue
        if not isinstance(pid, str) or not pid.isdigit():
            continue
        product = {"id": pid, "name": obj["name"], "price": price,
                   "url": url, "image": image, "topic": topic,
                   "source": obj.get("last_source"), "checked_at": raw_ts,
                   "category": category, "category_fallback": fallback}
        results.append(product)
    from api_radar.quality import extract_exact_identity
    # Prefer offers with exact productId+itemId already visible in the
    # official API URL. Never resolve opaque referral links by clicking them.
    results.sort(key=lambda p: (
        extract_exact_identity(p["url"], p["id"]) is None,
        p["category_fallback"],
        CURATED_CATEGORY_PRIORITY.index(p["category"])
        if p["category"] in CURATED_CATEGORY_PRIORITY else 99,
        p["source"] != "goldbox", abs(p["price"] - 65000), p["id"]))
    return results[:CANDIDATE_LIMIT]


def compose_article(product: dict, history: dict, trend: dict | None,
                    image: Path, issued: str) -> dict:
    from gh_only.naver_blog_jirum import DISCLOSURE, naver_title
    from gh_only.naver_blog_format import plain_body, validate_blocks
    from gh_only.coupang_partners_api import issued_link_url
    if not (history.get("exact_product_item_verified") is True
            and (trend is None or (trend.get("verified") is True and trend.get("demand_uptrend") is True))
            and isinstance(image, Path) and image.is_file()
            and issued_link_url(issued)):
        raise QualityError("ARTICLE_GATES_NOT_VERIFIED")
    name = product["name"]
    price = product["price"]
    ref_price = history["reference_price"]
    percentage = history["discount_pct"]
    basis = history["history_window"]
    ref_label = (
        f"최근 {basis.split('_')[1]}주 관측 평균" if basis.startswith("recent_")
        else "관측 기간이 명시되지 않은 전체 평균"
    )
    # No claim of product-specific NAVER traffic; the trend is topical.
    blocks = [
        {"type": "text", "text": DISCLOSURE, "bold_prefix": "", "kind": "disclosure"},
        {"type": "image", "kind": "hero_photo"},
        {"type": "text", "text": "🛍️ 쿠팡 상품 가격 확인", "bold_prefix": "🛍️ 쿠팡 상품 가격 확인", "kind": "heading"},
        {"type": "text", "text": name, "bold_prefix": "", "kind": "product"},
        {"type": "text", "text": f"💸 로우차트 {ref_label} : 약 {ref_price:,}원",
         "bold_prefix": "💸 로우차트", "kind": "reference"},
        {"type": "text", "text": f"🔥 오늘 특가 : 약 {price:,}원 (관측 평균 대비 약 {percentage:.1f}% 저렴)",
         "bold_prefix": "🔥 오늘 특가", "kind": "price"},
        {"type": "text", "text": "📊 가격 비교는 로우차트 동일 상품·옵션 관측값 기준이며 쿠팡 공식 정가가 아닙니다.",
         "bold_prefix": "📊 가격 비교", "kind": "source_basis"},
        *([{"type": "text", "text": f"🔎 네이버쇼핑에서 '{trend['keyword']}' 관련 클릭지수의 최근 7일 평균은 "
             f"{trend['recent_seven_day_mean']:.1f}입니다. 이는 상대 지수이며 검색량이나 판매량이 아닙니다.",
             "bold_prefix": "🔎 네이버쇼핑", "kind": "search_context"}]
          if trend is not None else []),
        {"type": "text", "text": "※ 상품 가격·배송조건은 확인 시점에 따라 변할 수 있으며, "
         "할인·품절 여부는 구매 직전 상품 페이지에서 다시 확인해 주세요.",
         "bold_prefix": "", "kind": "note"},
        {"type": "text", "text": "📦 쿠팡에서 상품정보 더보기",
         "bold_prefix": "📦 쿠팡에서 상품정보 더보기",
         "kind": "affiliate_link", "link_url": issued},
    ]
    validate_blocks(blocks, issued)
    title = naver_title("[쿠팡🔥핫딜] " + name + " 가격 비교")
    return {"title": title, "body": plain_body(blocks),
            "blocks": blocks, "image": image, "link": issued, "grade": "A"}


def goldbox_title(name: str) -> str:
    """Independent B-grade title fit; never re-label as a verified hot deal."""
    from gh_only.naver_blog_jirum import NAVER_TITLE_MAX_UTF16_UNITS
    prefix = "[쿠팡🎁골드박스] "
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not name:
        raise QualityError("GOLDBOX_NAME_MISSING")
    def units(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2
    if units(prefix + name) <= NAVER_TITLE_MAX_UTF16_UNITS:
        return prefix + name
    remaining = NAVER_TITLE_MAX_UTF16_UNITS - units(prefix) - units("…")
    trimmed = ""
    for char in name:
        if units(trimmed + char) > remaining:
            break
        trimmed += char
    if units(trimmed.strip()) < 8:
        raise QualityError("GOLDBOX_TITLE_TOO_LONG")
    return prefix + trimmed.rstrip() + "…"


def compose_goldbox_article(product: dict, image: Path, issued: str) -> dict:
    """Official Goldbox listing: verified observed API price, NEVER a fake discount."""
    from gh_only.naver_blog_jirum import DISCLOSURE
    from gh_only.naver_blog_format import plain_body, validate_blocks
    from gh_only.coupang_partners_api import issued_link_url
    price = product.get("price")
    if (product.get("source") != "goldbox"
            or product.get("category") not in CURATED_CATEGORY_PRIORITY
            or type(price) is not int or not 5000 <= price <= 350000
            or not isinstance(image, Path) or not image.is_file()
            or not issued_link_url(issued)):
        raise QualityError("GOLDBOX_ARTICLE_GATES_UNVERIFIED")
    blocks = [
        {"type": "text", "text": DISCLOSURE, "bold_prefix": "", "kind": "disclosure"},
        {"type": "image", "kind": "hero_photo"},
        {"type": "text", "text": "🎁 쿠팡 공식 골드박스 특가 상품",
         "bold_prefix": "🎁 쿠팡 공식 골드박스 특가 상품", "kind": "heading"},
        {"type": "text", "text": product["name"], "bold_prefix": "", "kind": "product"},
        {"type": "text", "text": f"💰 수집 당시 쿠팡 판매가 : 약 {price:,}원",
         "bold_prefix": "💰 수집 당시 쿠팡 판매가", "kind": "price"},
        {"type": "text",
         "text": "🔎 쿠팡 공식 골드박스 API에 등록된 상품으로 확인했습니다. 과거 평균가격 대비 할인율은 검증되지 않아 표시하지 않습니다.",
         "bold_prefix": "🔎 쿠팡 공식 골드박스", "kind": "source_basis"},
        {"type": "text",
         "text": "※ 골드박스 할인 혜택은 와우 멤버십 조건에 따라 달라질 수 있습니다.",
         "bold_prefix": "", "kind": "membership_note"},
        {"type": "text",
         "text": "※ 판매가·재고·할인 적용 여부는 수시로 바뀔 수 있으니 구매 직전 상품 페이지에서 다시 확인해 주세요.",
         "bold_prefix": "", "kind": "note"},
        {"type": "text", "text": "📦 쿠팡에서 상품정보 더보기",
         "bold_prefix": "📦 쿠팡에서 상품정보 더보기",
         "kind": "affiliate_link", "link_url": issued},
    ]
    validate_blocks(blocks, issued)
    return {"title": goldbox_title(product["name"]),
            "body": plain_body(blocks), "blocks": blocks,
            "image": image, "link": issued, "grade": "B"}


def claim(state: dict, pid: str, source: dict, now: datetime, *, grade="A") -> dict:
    """No browser work until an immutable pre-publish claim is durable."""
    if pid in state.get("published", {}):
        raise RadarStateError("RADAR_ALREADY_PUBLISHED")
    claims = state.setdefault("api_publish_claims", {})
    if pid in claims:
        raise RadarStateError("RADAR_CLAIM_ALREADY_EXISTS_SAFE_STOP")
    if daily_slot_count(state, now) >= MAX_PUBLISH_PER_KST_DAY:
        raise RadarStateError("RADAR_DAILY_POST_CAP_REACHED")
    saved = state.get("products", {}).get(pid)
    if not isinstance(saved, dict) or saved.get("last_price") != source["price"]:
        raise RadarStateError("RADAR_SOURCE_CHANGED_BEFORE_PUBLISH")
    if saved.get("last_checked_at_utc") != source["checked_at"]:
        raise RadarStateError("RADAR_SOURCE_TIMESTAMP_CHANGED")
    if grade not in ("A", "B"):
        raise RadarStateError("RADAR_GRADE_INVALID")
    claims[pid] = {
        "grade": grade,
        "status": "MANUAL_REVIEW_ON_ANY_UNCERTAIN_FAILURE",
        "run_id": os.getenv("GITHUB_RUN_ID", "unknown"),
        "claimed_at": now.isoformat(), "source_price": source["price"],
        "category": source.get("category", ""),
    }
    return state


def checkpoint(state: dict, pid: str, url: str, now: datetime, *, grade="A") -> dict:
    if pid not in state.get("api_publish_claims", {}):
        raise RadarStateError("RADAR_CLAIM_NOT_FOUND")
    if pid in state.get("published", {}):
        raise RadarStateError("RADAR_DUPLICATE_RECEIPT")
    if grade not in ("A", "B") or state["api_publish_claims"][pid].get("grade", "A") != grade:
        raise RadarStateError("RADAR_GRADE_RECEIPT_MISMATCH")
    state["published"][pid] = {
        "grade": grade,
        "url": url, "at": now.isoformat(),
        "kst_date": now.astimezone(KST).date().isoformat(),
        "source": "api_radar",
        "category": state.get("products", {}).get(pid, {}).get("category", ""),
    }
    state["api_publish_claims"].pop(pid)
    return state


def run(*, preview=False, now=None) -> int:
    """A first; B official Goldbox fallback; combined max 3/KST day.

    Two bounded passes avoid false discount percentages while offering a
    truthful listing fallback when no independently verified deal exists.
    """
    if os.getenv(BLOG_PUBLICATION_ENABLED, "false").lower() != "true":
        return safe_stop("OWNER_PUBLISH_SWITCH_DISABLED")
    trend_mode = os.getenv("API_RADAR_TREND_MODE", "curated").lower()
    if trend_mode not in ("curated", "hub"):
        return safe_stop("INVALID_TREND_MODE")
    if trend_mode == "hub" and (
            not os.getenv("NAVER_API_HUB_KEY_ID") or not os.getenv("NAVER_API_HUB_KEY")):
        return safe_stop("NAVER_SHOPPING_INSIGHT_KEYS_NOT_CONFIGURED")
    if not os.getenv("NAVER_BLOG_STORAGE_STATE_JSON"):
        return safe_stop("NAVER_LOGIN_SESSION_NOT_CONFIGURED")
    if not os.getenv("COUPANG_PARTNERS_ACCESS_KEY") or not os.getenv("COUPANG_PARTNERS_SECRET_KEY"):
        return safe_stop("COUPANG_PARTNERS_API_KEYS_NOT_CONFIGURED")

    now = now or utc_now()
    state, _ = load_state()
    slots_used = daily_slot_count(state, now)
    if slots_used >= MAX_PUBLISH_PER_KST_DAY:
        return safe_stop("KST_DAILY_VERIFIED_POST_LIMIT_REACHED")
    candidates = select_candidates(state, now, trend_mode=trend_mode)
    if not candidates:
        return safe_stop("NO_FRESH_UNPOSTED_PURCHASE_CANDIDATES")

    posts = 0
    a_checks = 0
    b_checks = 0
    rejection_codes = {}
    posted_in_run = set()
    max_posts = min(MAX_PUBLISH_PER_RUN, MAX_PUBLISH_PER_KST_DAY - slots_used)

    for grade in ("A", "B"):
        if posts >= max_posts:
            break
        for candidate in candidates:
            if posts >= max_posts:
                break
            if candidate["id"] in posted_in_run:
                continue
            if grade == "A":
                if candidate["price"] < 15000:
                    continue
                if a_checks >= (1 if trend_mode == "hub" else MAX_A_HISTORY_CHECKS_PER_RUN):
                    break
                a_checks += 1
            else:
                # B may be published ONLY when the last observation comes
                # directly from the official Goldbox API (never SEARCH).
                if (candidate["source"] != "goldbox"
                        or candidate["category"] not in CURATED_CATEGORY_PRIORITY):
                    continue
                b_checks += 1
            keyword, category = candidate["topic"]
            trend = None
            if grade == "A" and trend_mode == "hub":
                try:
                    trend = fetch_topic_trend(keyword, category, now=now)
                except TrendError as exc:
                    return safe_stop("TREND_" + str(exc))
                if not trend["demand_uptrend"]:
                    rejection_codes["NO_VERIFIED_TOPIC_DEMAND_UPTREND"] = (
                        rejection_codes.get("NO_VERIFIED_TOPIC_DEMAND_UPTREND", 0) + 1)
                    continue
            try:
                identity = resolve_exact_identity(candidate)
                history = check_exact_history(candidate, identity) if grade == "A" else None
            except QualityError as exc:
                code = grade + "_" + str(exc)
                rejection_codes[code] = rejection_codes.get(code, 0) + 1
                continue
            if preview:
                print("API_RADAR_PUBLISH_PREVIEW_OK posts=0 grade=" + grade
                      + " exact_item=true history_verified=" + str(grade == "A").lower())
                return 0
            try:
                path = download_verified_image(candidate["image"], WORK_DIR, candidate["id"])
                from gh_only.coupang_partners_api import create_product_deeplink
                receipt = create_product_deeplink(identity, subid="naverblog")
                article = (compose_article(candidate, history, trend, path, receipt["partners_link"])
                           if grade == "A" else
                           compose_goldbox_article(candidate, path, receipt["partners_link"]))
            except (QualityError, ValueError, RuntimeError) as exc:
                code = grade + "_PREPARATION_" + type(exc).__name__
                rejection_codes[code] = rejection_codes.get(code, 0) + 1
                continue

            latest, sha = load_state()
            if daily_slot_count(latest, utc_now()) >= MAX_PUBLISH_PER_KST_DAY:
                print("API_RADAR_POST_LIMIT_REACHED_WHILE_RECHECKING")
                break
            pid = candidate["id"]
            if pid in latest.get("published", {}) or pid in latest.get("api_publish_claims", {}):
                continue
            saved = latest.get("products", {}).get(pid)
            if (not isinstance(saved, dict)
                    or saved.get("last_price") != candidate["price"]
                    or saved.get("last_checked_at_utc") != candidate["checked_at"]
                    or saved.get("last_source") != candidate["source"]):
                rejection_codes["PRODUCT_CHANGED"] = rejection_codes.get("PRODUCT_CHANGED", 0) + 1
                continue
            claim(latest, pid, candidate, utc_now(), grade=grade)
            save_state(latest, sha)  # durable claim BEFORE browser submit

            from gh_only.naver_blog_deal_publisher import submit_one
            try:
                published_url = submit_one(
                    article, os.environ["NAVER_BLOG_STORAGE_STATE_JSON"], publish=True)
            except Exception as exc:
                print("API_RADAR_SUBMIT_UNCERTAIN_CLAIM_LOCKED error_type="
                      + type(exc).__name__ + " posts_confirmed=" + str(posts))
                return 1  # an uncertain Naver submit must never auto-retry
            if (not isinstance(published_url, str)
                    or not published_url.startswith("https://blog.naver.com/mw5114/")):
                raise RadarStateError("RADAR_NAVER_RESULT_UNCONFIRMED_REVIEW_REQUIRED")
            final, final_sha = load_state()
            checkpoint(final, pid, published_url, utc_now(), grade=grade)
            save_state(final, final_sha)
            posts += 1
            posted_in_run.add(pid)
            print("API_RADAR_BLOG_PUBLISHED post_id=" + pid + " grade=" + grade
                  + " posts_confirmed_in_run=" + str(posts))

    print("API_RADAR_PUBLISH_BATCH_RESULT posts=" + str(posts)
          + " a_checked=" + str(a_checks) + " b_checked=" + str(b_checks)
          + " rejected=" + json.dumps(rejection_codes, sort_keys=True)
          + " daily_cap=" + str(MAX_PUBLISH_PER_KST_DAY))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(preview=os.getenv("API_RADAR_PREVIEW_ONLY") == "true"))
    except (RadarStateError, QualityError, TrendError) as exc:
        print("API_RADAR_PUBLISH_HARD_STOP " + str(exc) + " posts=0_or_unconfirmed")
        raise SystemExit(1) from None
