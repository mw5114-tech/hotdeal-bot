"""Evidence-only NAVER Shopping Insight -> Coupang two-product comparison.

Shopping Insight returns relative click indices for a curated keyword set, not
absolute search volume or the top queries on Naver. A publish needs two real
Coupang search products, two officially minted affiliate links, and a receipt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from urllib import request, error

from api_radar.collector import SEARCH, get_api, extract_rows, RadarApiError
from api_radar.quality import QualityError
from growth_lab.comparison_photos import choose_photo, probe_naver_photo
from api_radar.publisher import cooldown_remaining_seconds
from api_radar.state_store import load_state, RadarStateError
from growth_lab.seo_drafts import products_for_comparison
from growth_lab.github_store import load, save, GrowthStoreError
from gh_only.coupang_partners_api import (
    create_product_deeplink, request_deeplink, issued_link_url, CoupangPartnersError,
)

KST = timezone(timedelta(hours=9))
PATH = "state/growth_lab_trend_comparisons.json"
SCHEMA = "growth-trend-comparison-v1"
API_URL = "https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords"
# Within each group, indices share one comparable scale. Never describe this
# curated set as Naver's overall trending-search ranking.
GROUPS = (
    ("IT/가전", "50000003",
     ("가습기", "외장 SSD", "무선 키보드", "보조배터리", "에어프라이어")),
    ("생활/건강", "50000008",
     ("캡슐세제", "물티슈", "세탁세제", "핸드워시", "전기요")),
)
WORK_DIR = Path("comparison-local")
DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."


class ComparisonError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def shop_payload(category: str, terms: tuple[str, ...], now: datetime) -> dict:
    yesterday = now.astimezone(KST).date() - timedelta(days=1)
    return {
        "startDate": (yesterday - timedelta(days=27)).isoformat(),
        "endDate": yesterday.isoformat(),
        "timeUnit": "date",
        "category": category,
        "keyword": [{"name": t, "param": [t]} for t in terms],
    }


def shopping_index(category: str, terms: tuple[str, ...], now: datetime,
                   *, opener=None) -> dict:
    key_id = os.getenv("NAVER_API_HUB_KEY_ID", "").strip()
    secret = os.getenv("NAVER_API_HUB_KEY", "").strip()
    if not key_id or not secret:
        raise ComparisonError("NAVER_HUB_KEYS_MISSING")
    if len(key_id) > 256 or len(secret) > 256 or any(c in key_id + secret for c in "\r\n"):
        raise ComparisonError("NAVER_HUB_KEYS_INVALID")
    payload = json.dumps(shop_payload(category, terms, now), ensure_ascii=False).encode()
    req = request.Request(API_URL, method="POST", data=payload, headers={
        "X-NCP-APIGW-API-KEY-ID": key_id,
        "X-NCP-APIGW-API-KEY": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with (opener or request.urlopen)(req, timeout=16) as response:
            if response.status != 200:
                raise ComparisonError("NAVER_SHOPPING_HTTP_NOT_200")
            raw = response.read(120001)
    except error.HTTPError as exc:
        raise ComparisonError("NAVER_SHOPPING_HTTP_" + str(exc.code)) from None
    except (error.URLError, OSError, TimeoutError):
        raise ComparisonError("NAVER_SHOPPING_NETWORK_FAILURE") from None
    if len(raw) > 120000:
        raise ComparisonError("NAVER_SHOPPING_BODY_OVERSIZE")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ComparisonError("NAVER_SHOPPING_BAD_JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ComparisonError("NAVER_SHOPPING_SCHEMA_INVALID")
    return data


def score_group(data: dict, terms: tuple[str, ...], category_name: str) -> list[dict]:
    """Scores only compare keywords requested together in the same category."""
    result = []
    for entry in data["results"]:
        if not isinstance(entry, dict) or entry.get("title") not in terms:
            continue
        points = entry.get("data")
        if not isinstance(points, list):
            continue
        by_date = {}
        for p in points:
            if not isinstance(p, dict) or not isinstance(p.get("period"), str):
                continue
            ratio = p.get("ratio")
            if type(ratio) in (int, float) and 0 <= ratio <= 100:
                by_date[p["period"]] = float(ratio)
        values = [by_date[k] for k in sorted(by_date)]
        if len(values) < 21:
            continue
        previous, recent = sum(values[-14:-7]) / 7, sum(values[-7:]) / 7
        growth = recent / previous if previous >= 2 else None
        # Popular OR demonstrably rising within the curated keyword panel.
        if recent < 25 or (recent < 45 and (growth is None or growth < 1.10)):
            continue
        result.append({
            "keyword": entry["title"], "category_name": category_name,
            "recent_index": round(recent, 2),
            "previous_index": round(previous, 2),
            "growth_ratio": round(growth, 3) if growth is not None else None,
            "rank_score": round(recent + min(max((growth or 1) - 1, 0), 0.5) * 25, 2),
            "relative_index_only": True,
        })
    return sorted(result, key=lambda x: (-x["rank_score"], x["keyword"]))


def ranked_topics(now: datetime) -> list[dict]:
    # Alternate group precedence by day, rather than making an invalid
    # cross-category comparison of independently normalized indices.
    groups = list(GROUPS)
    if now.astimezone(KST).date().toordinal() % 2:
        groups.reverse()
    output = []
    for label, cat, terms in groups:
        result = shopping_index(cat, terms, now)
        output.extend(score_group(result, terms, label)[:3])
    return output


def size_tokens(name: str) -> set[str]:
    return set(re.sub(r"\s+", "", m.group().upper())
               for m in re.finditer(r"(?<!\d)\d+(?:\.\d+)?\s*(?:TB|GB|ML|MAH|KG|L|매|개)\b",
                                    name, flags=re.I))


def choose_pair(offers: list[dict]) -> tuple[dict, dict] | None:
    """No option equivalence claims; refuse obvious different pack sizes."""
    for i, first in enumerate(offers):
        for second in offers[i + 1:]:
            if first["id"] == second["id"]:
                continue
            p, q = first["price"], second["price"]
            if q > p * 2.3:
                continue
            a, b = size_tokens(first["name"]), size_tokens(second["name"])
            if a != b:
                continue
            if first["image"] == second["image"]:
                continue
            return first, second
    return None


def pair_key(keyword: str, a: dict, b: dict) -> str:
    return keyword + ":" + ":".join(sorted((a["id"], b["id"])))


def compose(topic: dict, a: dict, b: dict, links: tuple[str, str],
            photo: Path, now: datetime) -> dict:
    if not all(issued_link_url(u) for u in links) or links[0] == links[1]:
        raise ComparisonError("COMPARISON_LINKS_NOT_VERIFIED")
    keyword = topic["keyword"]
    date = now.astimezone(KST).date().isoformat()
    # The lower displayed API price is not a universal cheaper option.
    title = "[쿠팡📊비교] " + keyword + " A vs B, 가격·배송 조건 비교"
    def ship(o: dict) -> str:
        return ("로켓배송 표시" if o["is_rocket"] else "로켓배송 확인되지 않음") + " / " + (
            "무료배송 표시" if o["free_shipping"] else "무료배송 확인되지 않음")
    paragraphs = [
        "네이버 쇼핑인사이트의 후보 검색어 클릭 추이를 확인한 뒤, 쿠팡 공식 파트너스 검색 API에서 찾은 두 상품을 비교했습니다. 전체 네이버 검색어 순위나 실제 월간 검색량은 아닙니다.",
        "비교 시점: " + date + " / 분야: " + topic["category_name"] + " / 품목: " + keyword,
        "상품 A: " + a["name"],
        "상품 A 수집 당시 검색 API 표시가격: " + format(a["price"], ",") + "원 / " + ship(a),
        "상품 B: " + b["name"],
        "상품 B 수집 당시 검색 API 표시가격: " + format(b["price"], ",") + "원 / " + ship(b),
        "비교 분석: 같은 검색어로 조회된 서로 다른 상품입니다. A의 표시가격은 B보다 "
        + format(b["price"] - a["price"], ",")
        + "원 낮습니다. 다만 옵션·구성·규격이 동일하다는 뜻은 아닙니다.",
        "왜 A를 고려할까? 표시가격을 우선하고 상세 옵션이 필요한 조건과 맞는다면 A를 먼저 비교해 볼 수 있습니다. B 역시 실제 사양이나 구성에 따라 더 적합할 수 있습니다.",
        "구매 전 반드시 확인: 상세 페이지의 용량·구성·옵션, 실제 결제가격, 배송비 및 재고를 확인하세요. 제품 성능·품질·할인율·후기 우열은 검증되지 않아 주장하지 않습니다.",
    ]
    return {"title": title, "photo": photo, "paragraphs": paragraphs,
            "links": [{"label": "📦 상품 A 쿠팡에서 확인", "url": links[0]},
                      {"label": "📦 상품 B 쿠팡에서 확인", "url": links[1]}],
            "product_ids": [a["id"], b["id"]], "topic": keyword}


def tracked_link(p: dict) -> str:
    identity = p["identity"]
    if identity is not None:
        return create_product_deeplink(identity, subid="naverblog")["partners_link"]
    # Identifies a real product, but not an exact option/price.
    canonical = "https://www.coupang.com/vp/products/" + p["id"]
    return request_deeplink(canonical, identity=None, subid="naverblog")


def run(*, now: datetime | None = None, preview=False, requested_topic: str | None = None) -> int:
    now = now or now_utc()
    today = now.astimezone(KST).date().isoformat()
    state, sha = load(PATH, schema=SCHEMA)
    state = state or {"schema": SCHEMA, "posts": {}, "claims": {}}
    posts = state.get("posts")
    claims = state.get("claims")
    if not isinstance(posts, dict) or not isinstance(claims, dict):
        raise ComparisonError("COMPARISON_STATE_INVALID")
    if claims:
        print("TREND_COMPARISON_SAFE_STOP unresolved_claims=true")
        return 0
    if any(x.get("kst_date") == today for x in posts.values()):
        print("TREND_COMPARISON_ALREADY_POSTED_TODAY")
        return 0
    try:
        radar, _ = load_state()
        if not preview and cooldown_remaining_seconds(radar, now):
            print("TREND_COMPARISON_SAFE_STOP radar_cooldown=true")
            return 0
    except RadarStateError:
        raise
    topics = ranked_topics(now)
    if requested_topic:
        topics = [t for t in topics if t["keyword"] == requested_topic]
        if not topics:
            print("TREND_COMPARISON_REQUESTED_TOPIC_NOT_ELIGIBLE posts=0")
            return 0
    print("TREND_COMPARISON_CANDIDATE_TOPICS=" + str(len(topics)))
    image_failures = 0
    for t in topics:
        # No repeat topic within two weeks.
        if any(x.get("topic") == t["keyword"]
               and (now - datetime.fromisoformat(x["at"])).total_seconds() < 14 * 86400
               for x in posts.values() if isinstance(x, dict) and isinstance(x.get("at"), str)):
            continue
        body = get_api(SEARCH, {"keyword": t["keyword"], "limit": 10})
        offers = products_for_comparison(extract_rows(body), t["keyword"])
        pair = choose_pair(offers)
        if pair is None:
            continue
        a, b = pair
        key = pair_key(t["keyword"], a, b)
        if key in posts:
            continue
        print("TREND_COMPARISON_PAIR_READY category=" + t["category_name"]
              + " keyword=" + t["keyword"] + " prices_verified=observed_only"
              + " pair_count=2")
        photo = choose_photo(a, b, radar, WORK_DIR)
        if photo.get("status") != "IMAGE_SAVED":
            image_failures += 1
            print("TREND_COMPARISON_IMAGE_NOT_APPROVED posts=0")
        if preview or os.getenv("TREND_COMPARISON_PUBLISH_ENABLED", "false").lower() != "true":
            # Exercise the backup API even when Lowchart succeeds, in preview only.
            if photo.get("status") == "IMAGE_SAVED":
                probe_naver_photo(a)
            print("TREND_COMPARISON_PREVIEW_ONLY posts=0 image_ready=" + str(photo.get("status") == "IMAGE_SAVED").lower())
            return 0
        if photo.get("status") != "IMAGE_SAVED":
            continue
        if not os.getenv("NAVER_BLOG_STORAGE_STATE_JSON"):
            raise ComparisonError("NAVER_SESSION_MISSING")
        links = (tracked_link(a), tracked_link(b))
        article = compose(t, a, b, links, photo["path"], now)
        source_label = ("로우차트 상품 이미지" if photo["source"] == "lowchart_exact_item_public_page"
                        else "네이버 이미지 검색에서 확인한 쿠팡 공식 제휴 상품 이미지")
        article["paragraphs"].insert(0, "위 사진은 비교 대상 상품 " + photo["label"] + "의 " + source_label + "입니다.")
        # Claim before touching Naver: an ambiguous result cannot be re-posted.
        claims[key] = {"at": now_utc().isoformat(), "product_ids": article["product_ids"],
                       "status": "MANUAL_REVIEW_ON_UNCERTAIN_RESULT"}
        save(PATH, state, sha, schema=SCHEMA)
        from gh_only.naver_blog_comparison_publisher import submit_comparison
        try:
            receipt = submit_comparison(article, os.environ["NAVER_BLOG_STORAGE_STATE_JSON"])
        except Exception as exc:
            # Keep durable claim. No secret, URL or API response in error logs.
            print("TREND_COMPARISON_SUBMIT_UNCERTAIN type=" + type(exc).__name__)
            return 1
        if not isinstance(receipt, str) or not re.fullmatch(
                r"https://blog\.naver\.com/mw5114/\d+", receipt):
            print("TREND_COMPARISON_UNCONFIRMED_URL")
            return 1
        final, fresh_sha = load(PATH, schema=SCHEMA)
        if key not in final.get("claims", {}):
            raise ComparisonError("COMPARISON_CLAIM_LOST")
        final.setdefault("posts", {})[key] = {
            "at": now_utc().isoformat(), "kst_date": today, "topic": t["keyword"],
            "product_ids": article["product_ids"], "url": receipt,
            "relative_index": t["recent_index"],
            "source": "NAVER_SHOPPING_INSIGHT_AND_COUPANG_API",
        }
        final["claims"].pop(key)
        final["updated_at"] = now_utc().isoformat()
        save(PATH, final, fresh_sha, schema=SCHEMA)
        print("TREND_COMPARISON_PUBLISHED_CONFIRMED count=1")
        return 0
    if image_failures:
        print("TREND_COMPARISON_NO_APPROVED_IMAGE posts=0 pairs=" + str(image_failures))
        return 1
    print("TREND_COMPARISON_NO_VERIFIED_PAIR posts=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(preview=os.getenv("TREND_COMPARISON_PREVIEW_ONLY") == "true",
                             requested_topic=os.getenv("TREND_COMPARISON_TOPIC") or None))
    except (ComparisonError, RadarApiError, CoupangPartnersError,
            GrowthStoreError, RadarStateError, QualityError) as exc:
        # All errors in these types are fixed non-secret codes.
        print("TREND_COMPARISON_SAFE_FAILURE " + str(exc))
        raise SystemExit(1) from None
