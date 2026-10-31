"""Build review-only Daangn drafts. Never publishes or copies source affiliate URLs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from gh_only.pipeline import enrich_posts, fetch_public_html

CAFE_URL = "https://cafe.daangn.com/i-gagyeog-mos-c"
BOARD = "🔥 오늘의 핫딜"
DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
PRICE_LINE = re.compile(r"^[^가-힣A-Za-z0-9]*(?:특가|대박|초대박|무지성|판매가|최종가|할인가|와우가)\s*[:：]?\s*[\d,]+\s*원")
URL = re.compile(r"(?:https?://|www\.)\S+", re.I)


def clean_title(text):
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or PRICE_LINE.search(line):
            continue
        if URL.search(line) or any(marker in line for marker in (
            "바로가기", "가격 현황", "정상가:", "할인:", "선구매후고민",
            "파트너스 활동", "수수료", "브랜드커넥트")):
            continue
        return line[:180]
    return None


def build_drafts(html):
    # Reuse the source parser, but recompute title to avoid treating "특가 N원" as a name.
    from jirum_forward import parse_listing
    posts = {p["id"]: p for p in parse_listing(html)}
    result = []
    for deal in enrich_posts(html, max_posts=30):
        if deal["url_kind"] not in ("coupang_shortlink", "coupang_product"):
            continue
        title = clean_title(posts[deal["source_message_id"]]["text"])
        price = deal["sale_price_krw"]
        reasons = ["OWN_AFFILIATE_LINK_REQUIRED", "EXACT_OPTION_NOT_VERIFIED",
                   "DAANGN_GITHUB_SESSION_NOT_VALIDATED"]
        if not title:
            reasons.append("PRODUCT_TITLE_UNAVAILABLE")
        if price is None:
            reasons.append("SOURCE_PRICE_UNAVAILABLE")
        price_text = f"{price:,}원" if price is not None else "가격 확인 필요"
        body = "\n".join([
            DISCLOSURE, "",
            f"상품: {title or '상품명 확인 필요'}",
            f"원문에 표시된 가격: {price_text}",
            "현재 판매가격·옵션·수량·배송비·쿠폰 조건은 구매 전 확인해주세요.",
            "",
            "구매 링크: [동일 상품·옵션의 본인 파트너스 링크 확인 후 입력]",
        ])
        result.append({
            "source_message_id": deal["source_message_id"],
            "source_post_url": deal["source_post_url"],
            "target_cafe": CAFE_URL, "target_board": BOARD,
            "title": f"[쿠팡] {title or '상품명 확인 필요'}",
            "body": body, "status": "BLOCKED", "publish_ready": False,
            "blockers": reasons,
            "price_provenance": "telegram_source_post_not_live_price",
            "image_included": False,
        })
    return result


def run(output, fixture=None):
    html = Path(fixture).read_text(encoding="utf-8") if fixture else fetch_public_html()
    drafts = build_drafts(html)
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "posts_published": 0, "drafts": drafts}
    (root / "drafts.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Plain text prevents untrusted source text from becoming active Markdown/HTML.
    blocks = ["검토용 초안 — 실제 게시 0건", "본인 링크 미발급: 모든 초안 게시 보류", ""]
    for d in drafts:
        blocks.extend([f"원문 번호: {d['source_message_id']}", d["title"], d["body"], "\n" + "-" * 40])
    (root / "drafts.txt").write_text("\n".join(blocks), encoding="utf-8")
    print(f"DRAFTS={len(drafts)} PUBLISHED=0 ALL_BLOCKED=true")
    return len(drafts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="daangn-drafts")
    parser.add_argument("--fixture")
    args = parser.parse_args()
    run(args.output, args.fixture)
