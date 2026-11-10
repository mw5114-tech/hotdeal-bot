"""Readable, truthful Naver SmartEditor hotdeal copy.

Price labels preserve each reference's provenance. Fallcent high, midpoint,
current and low are never described as official list/normal prices.
Blocks are structured so SmartEditor can apply native bold and spacing.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from gh_only.naver_blog_jirum import DISCLOSURE

SOURCES = {
    "lowchart_public_history": ("로우차트 관측 평균", "로우차트 평균 대비"),
    "fallcent_observed_average": ("폴센트 관측 평균", "폴센트 평균 대비"),
    "fallcent_observed_high": ("폴센트 관측 최고가", "폴센트 최고가 대비"),
    "fallcent_high_low_midpoint": (
        "폴센트 최고·최저 중간값", "폴센트 최고·최저 중간값 대비"
    ),
    "fallcent_observed_current": ("폴센트 관측 현재가", "폴센트 관측 현재가 대비"),
    "fallcent_observed_low": ("폴센트 관측 최저가", "폴센트 관측 최저가 대비"),
}


def price_line(value: int | None) -> str:
    return f"약 {value:,}원" if type(value) is int and value > 0 else "확인되지 않음"


def _block(text: str, *, label: str = "", kind: str = "text") -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise ValueError("NAVER_FORMAT_EMPTY_OR_LONG_BLOCK")
    if label and not text.startswith(label):
        raise ValueError("NAVER_FORMAT_LABEL_MISMATCH")
    return {"type": "text", "text": text, "bold_prefix": label, "kind": kind}


def build_blocks(draft: dict, verified: dict, source: dict,
                 *, manual_test_replay: bool = False) -> list[dict]:
    caption = draft.get("caption") or {}
    sale = caption.get("sale_price_krw")
    comparison = caption.get("comparison_reference_price_krw")
    basis = caption.get("comparison_reference_source")
    discount = caption.get("approx_discount_vs_reference_pct")
    if type(sale) is not int or sale <= 0:
        raise ValueError("NAVER_FORMAT_SOURCE_PRICE_UNVERIFIED")
    link = verified.get("candidate_url")
    parsed = urlsplit(link if isinstance(link, str) else "")
    if (parsed.scheme != "https" or parsed.hostname != "link.coupang.com"
            or parsed.path != "/re/AFFSDP"):
        raise ValueError("NAVER_FORMAT_AFFILIATE_LINK_INVALID")
    telegram = source.get("source_post_url", "")
    if not isinstance(telegram, str) or not re.fullmatch(
        r"https://t\.me/jirum/[0-9]+", telegram
    ):
        raise ValueError("NAVER_FORMAT_SOURCE_MISSING")
    if not isinstance(verified.get("image"), Path):
        raise ValueError("NAVER_FORMAT_PRODUCT_IMAGE_REQUIRED")

    product = caption.get("product_name")
    if not isinstance(product, str) or not product.strip():
        raise ValueError("NAVER_FORMAT_PRODUCT_NAME_MISSING")
    product = product.strip()[:120]
    label, pct_basis = SOURCES.get(basis, ("확인되지 않음", ""))
    if comparison is not None and (
        type(comparison) is not int or comparison <= 0
    ):
        raise ValueError("NAVER_FORMAT_COMPARISON_INVALID")

    blocks = [
        _block(DISCLOSURE, kind="disclosure"),
        {"type": "image", "kind": "hero_photo"},
        _block("🛍️ 실시간 쿠팡 핫딜", label="🛍️ 실시간 쿠팡 핫딜", kind="heading"),
        _block(product, kind="product"),
    ]

    # Do not represent Fallcent HIGH or Naver search as a usual price.
    if comparison is not None and basis == "lowchart_public_history":
        blocks.append(_block(
            f"💸 쿠팡 평소 판매가(로우차트 관측 평균) : {price_line(comparison)}",
            label="💸 쿠팡 평소 판매가", kind="reference"
        ))
    elif comparison is not None and basis == "fallcent_observed_average":
        blocks.append(_block(
            f"💸 쿠팡 평소 판매가(폴센트 관측 평균) : {price_line(comparison)}",
            label="💸 쿠팡 평소 판매가", kind="reference"
        ))
    elif comparison is not None and basis == "fallcent_observed_high":
        blocks.append(_block(
            f"💸 폴센트 관측 최고가 : {price_line(comparison)}",
            label="💸 폴센트 관측 최고가", kind="reference"
        ))
    elif comparison is not None and basis == "fallcent_high_low_midpoint":
        blocks.append(_block(
            f"💸 폴센트 최고·최저 중간값 : {price_line(comparison)}",
            label="💸 폴센트 최고·최저 중간값", kind="reference"
        ))
    elif comparison is not None and basis == "fallcent_observed_current":
        blocks.append(_block(
            f"💸 폴센트 관측 현재가 : {price_line(comparison)}",
            label="💸 폴센트 관측 현재가", kind="reference"
        ))
    elif comparison is not None and basis == "fallcent_observed_low":
        blocks.append(_block(
            f"💸 폴센트 관측 최저가 : {price_line(comparison)}",
            label="💸 폴센트 관측 최저가", kind="reference"
        ))
    elif comparison is not None and basis == "naver_general_search_first_shopping_card":
        # Old drafts may contain this source; never publish one after owner
        # explicitly restricted comparisons to Lowchart then Fallcent only.
        raise ValueError("NAVER_PRICE_REFERENCE_DISABLED")
    else:
        blocks.append(_block(
            "💸 평소 판매가 : 확인되지 않음", label="💸 평소 판매가",
            kind="reference"
        ))

    # Put the reference price first, then the deal price with its honest
    # reference-relative discount. Never invent a percent when history or
    # comparison price is missing. The inventory notice follows immediately.
    # Any reference-source caveat remains below the sale and notice.
    has_discount = (
        type(discount) is int and 1 <= discount <= 95
        and comparison is not None and bool(pct_basis)
    )
    sale_line = f"🔥 오늘 특가 : {price_line(sale)}"
    if has_discount:
        sale_line += f" [약 {discount}% 할인]"
    blocks.append(_block(sale_line, label="🔥 오늘 특가", kind="price"))
    blocks.append(_block(
        "※ 실시간 핫딜 특성상 준비된 재고가 소진되면 가격이 원래대로 인상되거나 품절될 수 있습니다.",
        kind="note"
    ))

    if comparison is not None and basis in SOURCES:
        blocks.append(_block(
            f"📊 가격 비교 기준 : {label} · 쿠팡 공식 정가 아님",
            label="📊 가격 비교 기준", kind="source_basis"
        ))
    if manual_test_replay:
        blocks.append(_block(
            "※ 자동화 서식 테스트 게시글입니다. 소개된 가격은 현재 판매가가 아닐 수 있습니다.",
            kind="test_notice"
        ))
    if draft.get("image_source") in (
        "fallcent_search_representative", "naver_web_first_image"
    ):
        blocks.append(_block(
            "※ 사진은 검색 결과의 대표 이미지이며 실제 상품·옵션과 다를 수 있습니다.",
            kind="image_notice"
        ))

    # Show one short, readable clickable label; never put the 100+ character
    # AFFSDP URL into public visible text. Store the full verified URL only as
    # structured metadata for Naver's native hyperlink editor.
    blocks.append({
        **_block("📦 쿠팡에서 상품정보 더보기",
                 label="📦 쿠팡에서 상품정보 더보기", kind="affiliate_link"),
        "link_url": link,
    })

    return blocks


def plain_body(blocks: list[dict]) -> str:
    return "\n\n".join(
        block["text"] for block in blocks if block.get("type") == "text"
    )


def validate_blocks(blocks: list[dict], expected_url: str) -> None:
    if not isinstance(blocks, list) or len(blocks) < 8:
        raise ValueError("NAVER_FORMAT_BLOCKS_MISSING")
    if blocks[0].get("text") != DISCLOSURE or blocks[1].get("type") != "image":
        raise ValueError("NAVER_FORMAT_DISCLOSURE_IMAGE_ORDER")
    if sum(x.get("type") == "image" for x in blocks) != 1:
        raise ValueError("NAVER_FORMAT_IMAGE_COUNT")
    link_blocks = [x for x in blocks if x.get("kind") == "affiliate_link"]
    if len(link_blocks) != 1 or blocks[-1] is not link_blocks[0]:
        raise ValueError("NAVER_FORMAT_LINK_COUNT_OR_POSITION")
    if (link_blocks[0].get("link_url") != expected_url
            or link_blocks[0].get("text") != "📦 쿠팡에서 상품정보 더보기"):
        raise ValueError("NAVER_FORMAT_LINK_TARGET_OR_LABEL")
    body = plain_body(blocks)
    if (expected_url in body or "https://link.coupang.com/" in body
            or body.count(link_blocks[0]["text"]) != 1 or len(body) > 9000):
        raise ValueError("NAVER_FORMAT_DUPLICATED_VISIBLE_URL")
    for block in blocks:
        if block.get("type") == "text":
            _block(block["text"], label=block.get("bold_prefix", ""))
