"""Price-drop V2 -> existing Naver Goldbox publisher's ONE shared posting lane.

Only verified 24h AND historical-median discounts are eligible. This module
uses the existing Radar claim/receipt/cooldown in the SAME workflow and mutex;
it never runs a second independent SmartEditor publisher.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os

from growth_lab import price_radar
from growth_lab.github_store import load as load_growth
from api_radar.quality import download_verified_image, QualityError
from api_radar.state_store import load_state, save_state, RadarStateError


def candidates(now: datetime, radar_state: dict | None = None) -> list[dict]:
    if radar_state is None:
        radar_state, _ = load_state()
    latest, _ = load_growth(price_radar.PATH, schema=price_radar.SCHEMA)
    if not latest:
        return []
    return price_radar.eligible_events(
        latest, now, published=radar_state.get("published", {}),
        claims=radar_state.get("api_publish_claims", {}))


def pending_count(radar_state: dict, now: datetime) -> int:
    rows = candidates(now, radar_state)
    print("GROWTH_V2_PENDING_COUNT=" + str(len(rows)))
    return len(rows)


def validated_offer(event: dict, rows: list[dict]) -> dict | None:
    """Fresh official API response must match exact product+option+vendor+price."""
    expected = (event.get("id"), event.get("item_id"),
                event.get("vendor_item_id"), event.get("title_signature"))
    for raw in rows:
        offer = price_radar.candidate(raw, event["category_id"])
        if not offer:
            continue
        actual = (offer["id"], offer["item_id"],
                  offer.get("vendor_item_id"), offer.get("title_signature"))
        if (actual == expected
                and offer["key"] == event["key"]
                and offer["price"] == event["price"]
                and offer["category_id"] == event["category_id"]):
            return offer
    return None


def compose_drop_article(event: dict, offer: dict, photo: Path, issued: str) -> dict:
    """Cite the prior observed median, never an official MSRP or fake discount."""
    from gh_only.naver_blog_jirum import DISCLOSURE, naver_title
    from gh_only.naver_blog_format import plain_body, validate_blocks
    from gh_only.coupang_partners_api import issued_link_url
    if (event.get("grade") not in ("S", "A")
            or type(event.get("prior_30d_median_krw")) is not int
            or type(event.get("price_24h_ago_krw")) is not int
            or type(offer.get("price")) is not int
            or not isinstance(photo, Path) or not photo.is_file()
            or not issued_link_url(issued)):
        raise QualityError("V2_ARTICLE_EVIDENCE_INVALID")
    pct_30 = event["drop_vs_30d_median_pct"]
    pct_24 = event["drop_vs_24h_pct"]
    if not (20 <= pct_30 <= 70 and 15 <= pct_24 <= 70
            and offer["price"] == event["price"]):
        raise QualityError("V2_ARTICLE_PRICE_OR_DROP_INVALID")
    price = offer["price"]
    blocks = [
        {"type":"text", "text":DISCLOSURE, "bold_prefix":"", "kind":"disclosure"},
        {"type":"image", "kind":"hero_photo"},
        {"type":"text", "text":"📉 실제 관측가격 급락 상품",
         "bold_prefix":"📉 실제 관측가격 급락 상품", "kind":"heading"},
        {"type":"text", "text":offer["name"], "bold_prefix":"", "kind":"product"},
        {"type":"text", "text":f"💸 최근 30일 내 일별 관측가 중앙값 : 약 {event['prior_30d_median_krw']:,}원",
         "bold_prefix":"💸 최근 30일 내", "kind":"reference"},
        {"type":"text", "text":f"🕐 약 하루 전 관측가 : 약 {event['price_24h_ago_krw']:,}원",
         "bold_prefix":"🕐 약 하루 전", "kind":"reference"},
        {"type":"text", "text":f"🔥 오늘 확인가 : 약 {price:,}원 (하루 전 대비 {pct_24:.1f}% 하락)",
         "bold_prefix":"🔥 오늘 확인가", "kind":"price"},
        {"type":"text", "text":f"📊 최근 관측 중앙값 대비 {pct_30:.1f}% 저렴합니다. 같은 상품·옵션의 실제 API 관측가격 기준이며 쿠팡 공식 정가 대비 할인율이 아닙니다.",
         "bold_prefix":"📊 최근 관측", "kind":"source_basis"},
        {"type":"text", "text":"※ API에 표시된 가격은 회원조건·쿠폰·배송지·옵션에 따라 실제 결제액과 다를 수 있습니다.",
         "bold_prefix":"", "kind":"membership_note"},
        {"type":"text", "text":"※ 실시간 특가 특성상 재고 소진 시 가격이 변하거나 품절될 수 있으니 구매 직전 재확인해 주세요.",
         "bold_prefix":"", "kind":"note"},
        {"type":"text", "text":"📦 쿠팡에서 상품정보 더보기",
         "bold_prefix":"📦 쿠팡에서 상품정보 더보기",
         "kind":"affiliate_link", "link_url":issued},
    ]
    validate_blocks(blocks, issued)
    suffix = f" [관측가 {pct_24:.0f}% 급락]"
    title = naver_title("[쿠팡🔥핫딜] " + offer["name"] + suffix)
    return {"title":title, "body":plain_body(blocks), "blocks":blocks,
            "image":photo, "link":issued, "grade":"A", "v2_grade":event["grade"]}


def run_one(*, now: datetime | None = None) -> int:
    """0=no signal or one confirmed post; 1=safe hard stop, claim retained."""
    from api_radar.publisher import cooldown_remaining_seconds, checkpoint, utc_now
    from gh_only.coupang_partners_api import create_product_deeplink, CoupangPartnersError
    from gh_only.naver_blog_deal_publisher import submit_one

    now = now or utc_now()
    state, _ = load_state()
    if cooldown_remaining_seconds(state, now):
        print("GROWTH_V2_COOLDOWN")
        return 0
    ranked = candidates(now, state)
    if not ranked:
        print("GROWTH_V2_NO_FRESH_VERIFIED_DROP posts=0")
        return 0
    event = ranked[0]  # one potential post, no bursts
    pid = event["id"]

    # Recheck live official offer; category absence and price increases both
    # fail closed. No Lowchart scraping, Naver session access or fake MSRP.
    try:
        offer = validated_offer(event,
                price_radar.fetch_category(event["category_id"]))
        if offer is None:
            print("GROWTH_V2_REVALIDATION_MISMATCH posts=0")
            return 0
        from api_radar.publisher import WORK_DIR
        photo = download_verified_image(offer["image"], WORK_DIR, pid)
        identity = {"product_id":offer["id"], "item_id":offer["item_id"],
                    "vendor_item_id":offer.get("vendor_item_id")}
        receipt = create_product_deeplink(identity, subid="naverblog")
        article = compose_drop_article(event, offer, photo, receipt["partners_link"])
    except (price_radar.PriceRadarError, QualityError, CoupangPartnersError,
            ValueError, RuntimeError) as exc:
        code = str(exc)
        if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 96:
            code = type(exc).__name__
        print("GROWTH_V2_PREP_REJECTED code=" + code + " posts=0")
        return 1

    # Re-read both states AFTER slow network work. A changed price, consumed
    # event, an existing Goldbox receipt or any uncertain claim is a stop.
    checked_at = utc_now()
    latest, sha = load_state()
    if (cooldown_remaining_seconds(latest, checked_at)
            or pid in latest.get("published", {})
            or pid in latest.get("api_publish_claims", {})
            or not any(x["key"] == event["key"]
                       and x["price"] == event["price"]
                       and x["observed_at"] == event["observed_at"]
                       for x in candidates(checked_at, latest))):
        print("GROWTH_V2_STATE_CHANGED_SAFE_STOP posts=0")
        return 0
    claims = latest.setdefault("api_publish_claims", {})
    claims[pid] = {"grade":"A",
                   "status":"MANUAL_REVIEW_ON_ANY_UNCERTAIN_FAILURE",
                   "run_id":os.getenv("GITHUB_RUN_ID", "unknown"),
                   "claimed_at":checked_at.isoformat(),
                   "source":"growth_price_drop_v2", "event_key":event["key"],
                   "source_price":offer["price"], "category":offer["category"]}
    save_state(latest, sha)  # claim BEFORE any editor submit, no auto retry

    try:
        url = submit_one(article, os.environ["NAVER_BLOG_STORAGE_STATE_JSON"], publish=True)
    except Exception as exc:
        print("GROWTH_V2_SUBMIT_UNCERTAIN_CLAIM_LOCKED error_type="
              + type(exc).__name__)
        return 1
    if not isinstance(url, str) or not url.startswith("https://blog.naver.com/mw5114/"):
        print("GROWTH_V2_UNCONFIRMED_POST_CLAIM_LOCKED")
        return 1
    confirmed, confirmed_sha = load_state()
    checkpoint(confirmed, pid, url, utc_now(), grade="A")
    confirmed["published"][pid]["source"] = "growth_price_drop_v2"
    confirmed["published"][pid]["v2_grade"] = event["grade"]
    confirmed["published"][pid]["category"] = offer["category"]
    confirmed["published"][pid]["event_key"] = event["key"]
    save_state(confirmed, confirmed_sha)
    print("GROWTH_V2_PUBLISHED product_id=" + pid + " grade=" + event["grade"])
    return 0
