"""Independent search-intent comparison DRAFT generator (never auto-posts).

Only official Coupang Partners search/API data; no invented specs, rankings,
reviews, historical discounts, search traffic or claimed SEO outcomes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re

from api_radar.collector import SEARCH, RadarApiError, get_api, extract_rows, normalize
from api_radar.quality import extract_exact_identity
from gh_only.coupang_partners_api import create_product_deeplink, issued_link_url, CoupangPartnersError
from growth_lab.github_store import load, save

KST = timezone(timedelta(hours=9))
PATH = "state/growth_lab_seo_drafts.json"
SCHEMA = "growth-seo-drafts-v1"
# Product groups are narrow enough to compare only actual listed prices.
TOPICS = (
    ("무선 키보드", "무선 키보드", "연결 방식·배열·키감"),
    ("전기포트", "전기포트", "용량·소재·소비전력"),
    ("외장 SSD", "외장 SSD", "용량·인터페이스·보증"),
    ("에어프라이어", "에어프라이어", "용량·세척·설치 공간"),
    ("보조배터리", "보조배터리", "용량·출력·항공 규정"),
    ("가습기", "가습기", "가습 방식·세척·물통 용량"),
    ("모니터암", "모니터암", "VESA 규격·지지 하중·책상 두께"),
    ("캡슐세제", "캡슐세제", "사용량·세탁기 호환·보관"),
)
MAX_DRAFTS = 90


class DraftError(RuntimeError):
    pass


def topic_for_day(now: datetime) -> tuple[str, str, str]:
    return TOPICS[now.astimezone(KST).date().toordinal() % len(TOPICS)]


def products_for_comparison(rows: list[dict], keyword: str) -> list[dict]:
    """Same-query candidates only. Do not assert equivalent size/capacity."""
    offers = []
    seen = set()
    for raw in rows:
        p = normalize(raw, "search")
        if not p or keyword.replace(" ", "").lower() not in p["name"].replace(" ", "").lower():
            continue
        identity = extract_exact_identity(p["url"], p["id"])
        if not identity or p["id"] in seen:
            continue
        seen.add(p["id"])
        offers.append({**p, "identity": identity})
    return sorted(offers, key=lambda x:(x["price"], x["id"]))[:5]


def build_draft(topic: tuple[str, str, str], offers: list[dict], now: datetime) -> dict:
    if len(offers) < 3:
        raise DraftError("SEO_NOT_ENOUGH_EXACT_ITEM_OFFERS")
    keyword, label, checks = topic
    date = now.astimezone(KST).date().isoformat()
    title = label + " 3종 가격 비교: 구매 전 확인할 점 (" + date + ")"
    preface = (
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n\n"
        "## " + title + "\n\n"
        + label + "을(를) 고를 때 상품명만 보고 결정하기보다, 옵션과 실제 결제 금액을 함께 확인해야 합니다. "
        + "아래 목록은 " + date + "에 쿠팡 공식 상품 검색 API로 확인된 **서로 다른 상품**을 "
        + "**수집 당시 판매가격 오름차순**으로 정리한 것입니다. "
        + "제품 성능이나 품질 순위가 아니며 동일 용량·옵션 비교를 보장하지 않습니다.\n\n"
        + "### 구매 전 확인할 기준\n\n"
        + checks.replace("·", ", ") + " 등을 각 판매 페이지에서 확인하세요. "
        + "검색 API에는 상세 스펙 검증 정보가 없어 여기에서 확정하지 않습니다.\n\n"
        + "### 수집 당시 가격 목록\n\n"
    )
    selected = offers[:3]
    lines = []
    for index, item in enumerate(selected, 1):
        if not issued_link_url(item.get("affiliate_url")):
            raise DraftError("SEO_OFFICIAL_LINK_MISSING")
        line = (
            "#### " + str(index) + ". " + item["name"] + "\n\n"
            + "- 수집 당시 쿠팡 가격: **" + format(item["price"], ",") + "원**\n"
            + "- 로켓배송 표시: " + ("예" if item["is_rocket"] else "확인되지 않음") + "\n"
            + "- 무료배송 표시: " + ("예" if item["free_shipping"] else "확인되지 않음") + "\n"
            + "- [쿠팡에서 실제 옵션·가격 확인](" + item["affiliate_url"] + ")\n\n"
        )
        lines.append(line)
    summary = (
        "### 어떤 상품을 선택해야 할까?\n\n"
        + "현재 검색 결과에서 가장 낮은 **표시가격**은 "
        + format(selected[0]["price"], ",") + "원이지만, "
        + "제품마다 구성품·용량·색상·옵션이 달라 단순 최저가만으로 추천할 수 없습니다. "
        + "필요한 조건을 먼저 정한 후 각 상세페이지에서 옵션과 총 결제금액을 비교하세요.\n\n"
        + "※ 할인율·정가·사용 후기·품질 우열은 확인되지 않아 주장하지 않습니다. "
        + "가격·재고·배송 조건은 예고 없이 달라질 수 있습니다.\n"
    )
    return {"key":date + ":" + keyword, "created_at":now.isoformat(),
            "topic": keyword, "title": title, "status":"DRAFT_REVIEW_REQUIRED",
            "auto_published":False, "product_ids":[x["id"] for x in selected],
            "article_markdown": preface + "".join(lines) + summary,
            "source":"official_coupang_affiliate_search",
            "claim_verified_option_equivalence":False,
            "claim_verified_seo_traffic":False}


def run(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    topic = topic_for_day(now)
    key = now.astimezone(KST).date().isoformat() + ":" + topic[0]
    old, sha = load(PATH, schema=SCHEMA)
    state = old or {"schema":SCHEMA, "drafts":[]}
    if not isinstance(state.get("drafts"), list):
        raise DraftError("SEO_DRAFT_STATE_INVALID")
    if any(isinstance(x, dict) and x.get("key")==key for x in state["drafts"]):
        print("GROWTH_SEO_ALREADY_DRAFTED")
        return 0
    body = get_api(SEARCH, {"keyword":topic[0], "limit":10})
    found = products_for_comparison(extract_rows(body), topic[0])
    if len(found)<3:
        print("GROWTH_SEO_SKIP exact_options=" + str(len(found)) +
              " minimum=3 status=NO_FAKE_COMPARISONS")
        return 0
    # Only official server-issued tracked links. This consumes <=3 link calls.
    for p in found[:3]:
        receipt = create_product_deeplink(p["identity"], subid="naverblog")
        p["affiliate_url"] = receipt["partners_link"]
    article = build_draft(topic, found, now)
    state["drafts"] = (state["drafts"] + [article])[-MAX_DRAFTS:]
    state["updated_at"] = now.isoformat()
    save(PATH, state, sha, schema=SCHEMA)
    print("GROWTH_SEO_DRAFT_READY topic_index=" + str(TOPICS.index(topic)) +
          " candidates=" + str(len(found)) + " posts=0 review_required=true")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (DraftError, RadarApiError, CoupangPartnersError) as exc:
        print("GROWTH_SEO_STOP " + str(exc))
        raise SystemExit(1) from None
