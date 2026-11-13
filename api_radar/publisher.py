"""Gated Coupang API Radar -> Naver Blog publisher.

Completely independent from Telegram, Daangn and the existing Naver FIFO.
One post at most per KST day. A durable claim is written BEFORE any submit;
uncertain failures are quarantined and never auto-retried.

Publishing requires: official Naver HUB demand trend + an API product that
resolves to an exact productId/itemId + Lowchart same-item verified price
history + a safe product image + an API-issued naverblog affiliate deeplink +
the existing authenticated SmartEditor session.

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
CANDIDATE_LIMIT = 4
FRESH_SECONDS = 2 * 3600
MAX_PUBLISH_PER_KST_DAY = 1
WORK_DIR = Path("api-radar-local")
BLOG_PUBLICATION_ENABLED = "API_RADAR_PUBLISH_ENABLED"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_stop(reason: str) -> int:
    print("API_RADAR_PUBLISH_SAFE_STOP " + reason + " posts=0")
    return 0


def select_candidates(state: dict, now: datetime) -> list[dict]:
    if state.get("api_publish_claims") and not isinstance(state["api_publish_claims"], dict):
        raise RadarStateError("RADAR_CLAIM_STATE_INVALID")
    posted = state.get("published", {})
    claims = state.get("api_publish_claims", {})
    if not isinstance(posted, dict) or not isinstance(claims, dict):
        raise RadarStateError("RADAR_PUBLISH_STATE_INVALID")
    today = now.astimezone(KST).date().isoformat()
    today_posts = sum(1 for p in posted.values() if
                      isinstance(p, dict) and p.get("kst_date") == today)
    if today_posts >= MAX_PUBLISH_PER_KST_DAY:
        return []
    results = []
    for pid, obj in state.get("products", {}).items():
        if pid in posted or pid in claims or not isinstance(obj, dict):
            continue
        topic = matching_topic(obj.get("name", ""))
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
        if (type(price) is not int or not 15000 <= price <= 350000
                or not safe_url(url) or not safe_url(image, image=True)):
            continue
        if not isinstance(pid, str) or not pid.isdigit():
            continue
        product = {"id": pid, "name": obj["name"], "price": price,
                   "url": url, "image": image, "topic": topic,
                   "source": obj.get("last_source"), "checked_at": raw_ts}
        results.append(product)
    results.sort(key=lambda p: (
        p["source"] != "goldbox", abs(p["price"] - 65000), p["id"]))
    return results[:CANDIDATE_LIMIT]


def compose_article(product: dict, history: dict, trend: dict,
                    image: Path, issued: str) -> dict:
    from gh_only.naver_blog_jirum import DISCLOSURE, naver_title
    from gh_only.naver_blog_format import plain_body, validate_blocks
    from gh_only.coupang_partners_api import issued_link_url
    if not (history.get("exact_product_item_verified") is True
            and trend.get("verified") is True and trend.get("demand_uptrend") is True
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
        {"type": "text", "text": f"🔎 네이버쇼핑에서 '{trend['keyword']}' 관련 클릭지수의 최근 7일 평균은 "
         f"{trend['recent_seven_day_mean']:.1f}입니다. 이는 상대 지수이며 검색량이나 판매량이 아닙니다.",
         "bold_prefix": "🔎 네이버쇼핑", "kind": "search_context"},
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
            "blocks": blocks, "image": image, "link": issued}


def claim(state: dict, pid: str, source: dict, now: datetime) -> dict:
    """No browser work until an immutable pre-publish claim is durable."""
    if pid in state.get("published", {}):
        raise RadarStateError("RADAR_ALREADY_PUBLISHED")
    claims = state.setdefault("api_publish_claims", {})
    if pid in claims:
        raise RadarStateError("RADAR_CLAIM_ALREADY_EXISTS_SAFE_STOP")
    saved = state.get("products", {}).get(pid)
    if not isinstance(saved, dict) or saved.get("last_price") != source["price"]:
        raise RadarStateError("RADAR_SOURCE_CHANGED_BEFORE_PUBLISH")
    if saved.get("last_checked_at_utc") != source["checked_at"]:
        raise RadarStateError("RADAR_SOURCE_TIMESTAMP_CHANGED")
    claims[pid] = {
        "status": "MANUAL_REVIEW_ON_ANY_UNCERTAIN_FAILURE",
        "run_id": os.getenv("GITHUB_RUN_ID", "unknown"),
        "claimed_at": now.isoformat(), "source_price": source["price"],
    }
    return state


def checkpoint(state: dict, pid: str, url: str, now: datetime) -> dict:
    if pid not in state.get("api_publish_claims", {}):
        raise RadarStateError("RADAR_CLAIM_NOT_FOUND")
    if pid in state.get("published", {}):
        raise RadarStateError("RADAR_DUPLICATE_RECEIPT")
    state["published"][pid] = {
        "url": url, "at": now.isoformat(),
        "kst_date": now.astimezone(KST).date().isoformat(),
        "source": "api_radar",
    }
    state["api_publish_claims"].pop(pid)
    return state


def run(*, preview=False, now=None) -> int:
    # No implicit self-activation from an environment shared with other jobs.
    if os.getenv(BLOG_PUBLICATION_ENABLED, "false").lower() != "true":
        return safe_stop("OWNER_PUBLISH_SWITCH_DISABLED")
    if not os.getenv("NAVER_API_HUB_KEY_ID") or not os.getenv("NAVER_API_HUB_KEY"):
        return safe_stop("NAVER_SHOPPING_INSIGHT_KEYS_NOT_CONFIGURED")
    if not os.getenv("NAVER_BLOG_STORAGE_STATE_JSON"):
        return safe_stop("NAVER_LOGIN_SESSION_NOT_CONFIGURED")
    if not os.getenv("COUPANG_PARTNERS_ACCESS_KEY") or not os.getenv("COUPANG_PARTNERS_SECRET_KEY"):
        return safe_stop("COUPANG_PARTNERS_API_KEYS_NOT_CONFIGURED")
    now = now or utc_now()
    state, _ = load_state()
    candidates = select_candidates(state, now)
    if not candidates:
        return safe_stop("NO_FRESH_UNPOSTED_TOPIC_CANDIDATES")
    # Bounded: at most one NAVER trend request, one item redirect check, and
    # one Lowchart lookup per run. Never attempt alternate candidates after
    # an uncertain failure, nor publish a second product.
    product = candidates[0]
    keyword, category = product["topic"]
    try:
        trend = fetch_topic_trend(keyword, category, now=now)
    except TrendError as exc:
        return safe_stop("TREND_" + str(exc))
    if not trend["demand_uptrend"]:
        return safe_stop("NO_VERIFIED_TOPIC_DEMAND_UPTREND")
    try:
        identity = resolve_exact_identity(product)
        history = check_exact_history(product, identity)
    except QualityError as exc:
        return safe_stop(str(exc))
    if preview:
        print("API_RADAR_PUBLISH_PREVIEW_OK posts=0 exact_item=true trend=true price=true")
        return 0
    try:
        path = download_verified_image(product["image"], WORK_DIR, product["id"])
        from gh_only.coupang_partners_api import create_product_deeplink
        receipt = create_product_deeplink(identity, subid="naverblog")
        article = compose_article(product, history, trend, path, receipt["partners_link"])
    except (QualityError, ValueError, RuntimeError) as exc:
        # Neither API key nor request/response bodies are printed.
        return safe_stop("PUBLISH_PREPARATION_NOT_VERIFIED_" + type(exc).__name__)
    # Reload immediately before durable claim: ensures stale state is rejected.
    latest, sha = load_state()
    if product["id"] in latest.get("published", {}) or product["id"] in latest.get("api_publish_claims", {}):
        return safe_stop("ALREADY_POSTED_OR_REVIEW_CLAIMED")
    claim(latest, product["id"], product, now)
    save_state(latest, sha)
    # IMPORTANT: never retry publication after a failed/ambiguous submit.
    from gh_only.naver_blog_deal_publisher import submit_one
    try:
        published_url = submit_one(article, os.environ["NAVER_BLOG_STORAGE_STATE_JSON"], publish=True)
    except Exception as exc:
        # Playwright exceptions can embed URLs / DOM diagnostics. Public Actions
        # logs receive ONLY the error class; the durable claim prevents retries.
        print("API_RADAR_SUBMIT_UNCERTAIN_CLAIM_LOCKED error_type="
              + type(exc).__name__)
        return 1
    # A write failure leaves the claim for manual review; never auto-click twice.
    if not isinstance(published_url, str) or not published_url.startswith("https://blog.naver.com/mw5114/"):
        raise RadarStateError("RADAR_NAVER_RESULT_UNCONFIRMED_REVIEW_REQUIRED")
    final, final_sha = load_state()
    checkpoint(final, product["id"], published_url, utc_now())
    save_state(final, final_sha)
    print("API_RADAR_BLOG_PUBLISHED post_id=" + product["id"] + " posts=1")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(preview=os.getenv("API_RADAR_PREVIEW_ONLY") == "true"))
    except (RadarStateError, QualityError, TrendError) as exc:
        print("API_RADAR_PUBLISH_HARD_STOP " + str(exc) + " posts=0_or_unconfirmed")
        raise SystemExit(1) from None
