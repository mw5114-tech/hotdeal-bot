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
from growth_lab.comparison_photos import choose_photo
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
            if first.get("image") and first["image"] == second.get("image"):
                continue
            return first, second
    return None


def pair_key(keyword: str, a: dict, b: dict) -> str:
    return keyword + ":" + ":".join(sorted((a["id"], b["id"])))


# Editorial buying-guide criteria, not measured product performance.
BUYING_CHECKS = {
    "가습기": ("침실이나 책상에서 쓸지", "물통 용량, 세척 편의성, 분무 방식과 소음"),
    "외장 SSD": ("사진·영상 백업용인지", "실제 용량, 연결 규격, 보증 기간과 전송 속도"),
    "무선 키보드": ("사무용인지 게임용인지", "배열, 스위치, 연결 방식과 배터리"),
    "보조배터리": ("외출할 때 휴대할 용도인지", "용량, 충전 출력, 무게와 케이블 호환성"),
    "에어프라이어": ("한 끼 분량인지 가족용인지", "조리 용량, 바스켓 세척 방식과 설치 공간"),
    "캡슐세제": ("평소 세탁량과 세탁기 종류", "캡슐 수량, 1회 사용량과 사용 가능 세탁기"),
    "물티슈": ("휴대용인지 집에서 쓸 용도인지", "매수, 크기, 원단과 성분"),
    "세탁세제": ("평소 세탁량과 세탁기 종류", "용량, 권장 사용량과 세탁기 호환성"),
    "핸드워시": ("본품이 필요한지 리필이 필요한지", "용량, 구성, 향과 성분"),
    "전기요": ("혼자 쓸지 침대에서 함께 쓸지", "크기, 온도 조절, 안전 인증과 세탁 방법"),
}


# Reader-centered hooks are general situations, never invented personal use.
EDITORIAL_ANGLES = {
    "가습기": ("난방을 틀고 자거나 책상에 오래 앉아 있다 보면 실내 건조함이 신경 쓰일 때가 있습니다.", "침실·책상용 가습기 고르는 법", "물통 용량과 세척 편의성", "침실이라면 소음 정보, 책상이라면 설치 공간과 급수 방법을 먼저 비교해 보세요. 자주 쓰는 제품인 만큼 물통을 얼마나 편하게 씻을 수 있는지도 중요합니다."),
    "외장 SSD": ("사진과 영상이 쌓이다 보면 컴퓨터나 노트북의 저장 공간이 부족해지기 마련입니다.", "백업용 외장 SSD 고르는 법", "용량과 연결 방식", "영상 파일을 자주 옮긴다면 연결 규격과 실제 지원 속도를, 장기 보관용이라면 보증 조건과 별도의 백업 방법을 살펴보세요."),
    "무선 키보드": ("책상 위 케이블을 줄이고 싶어도 키감이나 연결 방식이 맞지 않으면 오히려 불편해질 수 있습니다.", "사무용·게임용 무선 키보드 고르는 법", "키 배열과 연결 편의성", "문서 작업에는 필요한 키 배열을, 게임용이라면 연결 모드와 입력 관련 제조사 사양을 우선 확인하는 편이 좋겠습니다."),
    "보조배터리": ("외출 중 휴대폰 배터리가 부족해지면 충전할 곳부터 찾게 됩니다.", "외출용 보조배터리 고르는 법", "휴대성과 충전 출력", "하루 동안 들고 다닐 제품이라면 무게와 크기를, 여러 기기를 충전한다면 출력과 포트 호환성을 비교해 보세요."),
    "에어프라이어": ("냉동식품을 간단하게 데우거나 한 끼를 준비할 때 조리도구가 많아지면 설거지도 번거롭습니다.", "에어프라이어 용량과 세척 기준", "조리 용량과 관리 방법", "한두 사람 분량인지 가족용인지 먼저 정하고, 실제 바스켓 크기와 분리 세척 가능 여부를 살펴보세요."),
    "캡슐세제": ("빨래할 때마다 세제 양을 재는 과정이 번거롭게 느껴질 때가 있습니다.", "캡슐세제 구매 전 확인할 점", "세탁 횟수와 캡슐 구성", "총가격뿐 아니라 캡슐 수량과 권장 사용량을 확인해야 실제 세탁 한 번당 비용을 비교할 수 있습니다."),
    "물티슈": ("외출할 때나 집에서 물티슈가 필요할 때 크기와 포장 형태가 맞지 않으면 사용하기 불편할 수 있습니다.", "휴대용·가정용 물티슈 선택법", "매수와 원단", "휴대용은 포장 크기를, 집에서 쓸 제품은 장당 크기와 매수, 성분 정보를 살펴보는 편을 추천합니다."),
    "세탁세제": ("매일 빨래를 하다 보면 세제가 떨어지는 속도와 보관 공간도 신경 쓰이기 마련입니다.", "세탁세제 용량과 사용량 비교", "용량과 세탁기 호환성", "용량만으로 경제성을 판단하기보다는 권장 사용량과 세탁기 종류에 맞는 제품인지 확인해 보세요."),
    "핸드워시": ("손을 자주 씻는 집이라면 핸드워시가 금방 떨어져 리필을 준비하게 됩니다.", "핸드워시 본품·리필 선택법", "구성과 성분", "처음 구매하는 경우 용기 포함 여부를, 리필이 필요한 경우 호환성과 용량을 먼저 비교해 보세요."),
    "전기요": ("추운 날 침대에 누웠을 때 이불 속까지 따뜻해지는 데 시간이 걸리면 전기요를 알아보게 됩니다.", "전기요 크기와 관리 기준", "안전 인증과 온도 조절", "사용할 침대의 크기와 온도 조절 방식, 제조사가 안내하는 세탁 및 안전 사용 조건을 확인해 보세요."),
}


def compose(topic: dict, a: dict, b: dict, links: tuple[str, str],
            photo: Path, now: datetime) -> dict:
    if not all(issued_link_url(u) for u in links) or links[0] == links[1]:
        raise ComparisonError("COMPARISON_LINKS_NOT_VERIFIED")
    keyword = topic["keyword"]
    date = now.astimezone(KST).date().isoformat()
    situation, checks = BUYING_CHECKS.get(
        keyword, ("어떤 용도로 쓸지", "규격, 구성, 옵션과 배송 조건"))
    hook, angle, heading, guidance = EDITORIAL_ANGLES.get(
        keyword, ("제품을 구매할 때는 실제로 필요한 조건부터 살펴보는 것이 좋습니다.",
                  keyword + " 고르는 법", "구매 전 확인할 조건",
                  "사용 목적에 맞는 규격과 구성, 관리 방법을 확인해 보세요."))
    title = "[쿠팡📊비교] " + angle + ", 추천 제품 2가지"
    def ship(o: dict) -> str:
        parts = []
        if o["is_rocket"]:
            parts.append("로켓배송")
        if o["free_shipping"]:
            parts.append("무료배송")
        return ", ".join(parts) + " 표시" if parts else "배송비와 도착일은 상세 페이지에서 확인"
    def price(o: dict) -> str:
        return format(o["price"], ",") + "원"
    # Keep the original product/link association even when prices are reversed.
    lower, higher = sorted((a, b), key=lambda o: o["price"])
    if a["price"] == b["price"]:
        verdict = ("두 제품의 확인 당시 표시가격은 같습니다. 가격보다 "
                   + checks + "을 확인한 뒤 필요한 조건에 맞는 제품을 고르는 편을 추천합니다.")
    else:
        verdict = ("가격을 우선한다면 저는 " + lower["name"] + "을 먼저 살펴보겠습니다. "
                   + higher["name"] + "보다 확인 당시 "
                   + format(higher["price"] - lower["price"], ",")
                   + "원 저렴하게 표시됐습니다. 다만 구성이나 옵션이 다를 수 있어 "
                   "동일한 조건인지 확인한 후 결정하는 게 좋겠습니다.")
    paragraphs = [
        hook + " 이번에는 " + keyword + " 제품을 알아보는 분들이 구매 전에 참고할 만한 두 가지 후보를 정리해 보았습니다.",
        "직접 구매하거나 사용한 체험기는 아닙니다. 공개 상품 정보와 확인 당시 가격·배송 조건을 바탕으로 정리했습니다.",
        "먼저 살펴볼 구매 기준 | " + heading,
        guidance,
        "첫 번째 추천 후보 | " + a["name"],
        "확인 당시 가격은 " + price(a) + "입니다. 배송 조건: " + ship(a)
        + ". 필요한 구성과 옵션이 맞는지 확인해 보세요.",
        "두 번째 추천 후보 | " + b["name"],
        "확인 당시 가격은 " + price(b) + "입니다. 배송 조건: " + ship(b)
        + ". 첫 번째 후보와 실제 구성 및 규격을 비교해 보세요.",
        "저라면 어떤 제품을 고를까요?",
        verdict,
        "구매 전에 놓치기 쉬운 부분",
        "확인할 항목은 " + checks
        + "입니다. 실제 사용감이나 성능은 직접 검증하지 않았으므로 상세 정보와 실제 구매자 후기를 함께 참고하는 편을 추천합니다.",
        "아래 상품별 링크에서 현재 가격과 옵션을 확인할 수 있습니다. 위 가격은 "
        + date + " 확인 시점 기준으로 실제 결제가격과 다를 수 있습니다.",
    ]
    return {"title": title, "photo": photo, "paragraphs": paragraphs,
            "links": [{"label": "📦 상품 1 | " + a["name"] + " 가격 확인", "url": links[0]},
                      {"label": "📦 상품 2 | " + b["name"] + " 가격 확인", "url": links[1]}],
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
        offers = products_for_comparison(extract_rows(body), t["keyword"], require_image=False)
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
            print("TREND_COMPARISON_PREVIEW_ONLY posts=0 image_ready=" + str(photo.get("status") == "IMAGE_SAVED").lower())
            return 0
        if photo.get("status") != "IMAGE_SAVED":
            continue
        if not os.getenv("NAVER_BLOG_STORAGE_STATE_JSON"):
            raise ComparisonError("NAVER_SESSION_MISSING")
        links = (tracked_link(a), tracked_link(b))
        article = compose(t, a, b, links, photo["path"], now)
        source_label = ("로우차트 상품 이미지" if photo["source"] == "lowchart_exact_item_public_page"
                        else "네이버쇼핑 검색 결과에 표시된 상품 대표 이미지")
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
