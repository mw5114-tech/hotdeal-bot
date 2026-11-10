"""Default Jirum deal copy: product, historical observed reference, Telegram hot deal.

Pure functions only: no site access, posting, affiliate links, or credentials.
IMPORTANT: source "observed average" is NOT Coupang official list/normal price.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
import unicodedata

from gh_only.lowchart_price_reference import build_url

DEFAULT_PERCENT_STEP = 5
MAX_DISPLAY_DISCOUNT = 95


def nearest_five_discount(sale: int | None, historical_average: int | None) -> int | None:
    """Nearest 5 percentage points, HALF_UP; never invent a discount.

    E.g., 65.1% => 65%; 42.5% => 45%; 2% => no claim.
    A rounded 100% would falsely imply free goods; cap at 95%.
    """
    if (type(sale) is not int or type(historical_average) is not int
            or sale <= 0 or historical_average <= sale
            or historical_average > 100_000_000):
        return None
    percentage = Decimal(historical_average - sale) * 100 / Decimal(historical_average)
    scaled = (percentage / DEFAULT_PERCENT_STEP).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    rounded = int(scaled) * DEFAULT_PERCENT_STEP
    if rounded < DEFAULT_PERCENT_STEP:
        return None
    return min(rounded, MAX_DISPLAY_DISCOUNT)


def normalized_product_name(title: str | None) -> str:
    """Normalize common title punctuation and whitespace for cross-source identity."""
    if not title or not isinstance(title, str):
        return ""
    raw = title.split(" | 쿠팡")[0].split(" - 로우차트")[0]
    # Remove source metadata after a separated " - " delimiter.
    raw = re.split(r"\s+[-–]\s+", raw, maxsplit=1)[0]
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", raw).casefold())


SPEC_TOKEN = re.compile(
    r"(?<![0-9a-z])\d+(?:\.\d+)?\s*"
    r"(?:kg|g|ml|l|개|팩|입|봉|병|정|gb|tb|코어)(?![a-z])",
    re.I,
)


def _option_specs(title: str) -> tuple[str, ...]:
    """Extract sizes/counts from the original product-name portion only."""
    product = re.split(r"\s+[-–]\s+", title, maxsplit=1)[0]
    return tuple(re.sub(r"\s+", "", token).lower()
                 for token in SPEC_TOKEN.findall(product))


def same_named_option(og_title: str | None, history_title: str | None) -> bool:
    """Accept exact SKU with generic OG title vs detailed Lowchart option.

    Identities must ALSO match the exact productId/itemId separately in
    validated_history(). Do not accept conflicting sizes/capacities.
    """
    left, right = normalized_product_name(og_title), normalized_product_name(history_title)
    if len(left) < 9 or len(right) < 9:
        return False
    if left == right:
        return True
    if not right.startswith(left):
        return False
    # Kakao OG often only knows the product family, e.g.
    # "Apple 아이맥 24 M4칩"; Lowchart carries actual color/RAM/storage.
    # Permit prefix matches ONLY on exact product/item IDs verified elsewhere.
    left_specs = _option_specs(og_title or "")
    right_specs = _option_specs(history_title or "")
    return not left_specs or all(spec in right_specs for spec in left_specs)


def validated_history(history: dict | None, product_id: str, item_id: str,
                      og_title: str | None) -> int | None:
    if not isinstance(history, dict):
        return None
    if not (history.get("usable_historical_reference") is True
            and history.get("official_normal_price") is False
            and history.get("source_item_identifiers_present") is True
            and history.get("title_option_matches") is True
            and history.get("product_id") == product_id
            and history.get("item_id") == item_id
            and history.get("source_url") == build_url(product_id, item_id)
            and same_named_option(og_title, history.get("page_title"))):
        return None
    value = history.get("observed_average_krw")
    return value if type(value) is int and 0 < value <= 100_000_000 else None


def create_default_preview(sale: int | None, title: str | None,
                           average: int | None, source_id: int | None = None,
                           *, reference_source: str = "lowchart_public_history") -> dict:
    """Output only the user's chosen default copy; no fabricated source prices."""
    sale = sale if type(sale) is int and 0 < sale <= 100_000_000 else None
    average = average if type(average) is int and 0 < average <= 100_000_000 else None
    labels = {
        "lowchart_public_history": "💸 쿠팡 평소 판매가",
        "fallcent_observed_average": "💸 쿠팡 평소 판매가(폴센트 관측 평균)",
        "naver_general_search_first_shopping_card": "💸 비교 기준가(네이버 검색)",
        "fallcent_observed_high": "💸 폴센트 관측 최고가",
        "fallcent_high_low_midpoint": "💸 폴센트 최고·최저 중간값",
        "fallcent_observed_current": "💸 폴센트 관측 현재가",
        "fallcent_observed_low": "💸 폴센트 관측 최저가",
    }
    if reference_source not in labels:
        raise ValueError("INVALID_REFERENCE_PRICE_SOURCE")
    nonaverage_reference = reference_source not in (
        "lowchart_public_history", "fallcent_observed_average"
    )
    approx = nearest_five_discount(sale, average)
    # Requested three-line layout. The reference MUST remain clearly labeled
    # as Lowchart's observed historical average, not an official Coupang list price.
    # Use the SAME name portion in the 150-character Daangn headline and body.
    suffix = f" [{approx}%할인]" if approx is not None else ""
    max_product_chars = 150 - len("[쿠팡🔥핫딜] ") - len(suffix)
    product = (re.sub(r"\s+", " ", title).strip()[:max_product_chars].rstrip()
               if isinstance(title, str) and title.strip()
               else "상품명 확인 중")
    # First body line: discount FIRST, followed by Lowchart product name.
    # If Lowchart history cannot establish a discount, never invent a rate.
    body_discount = f"[{approx}%할인]" if approx is not None else "[할인율 확인 중]"
    lines = [f"{body_discount} {product}"]
    if average is not None:
        lines.append(f"{labels[reference_source]} : 약 {average:,}원")
    else:
        lines.append("💸 쿠팡 평소 판매가 : 확인 중")
    if sale is not None:
        price_text = f"🔥 핫딜 특가 : {sale:,}원"
        if approx is not None:
            price_text += f" (약 {approx}% 할인 / 상세페이지 참고)"
        else:
            price_text += " (상세페이지 참고)"
    else:
        price_text = "🔥 핫딜 특가 : 확인 중"
    lines.append(price_text)
    return {
        "source_message_id": source_id,
        "product_name": product,
        "sale_price_krw": sale,
        "sale_price_source": "telegram_jirum_message",
        # Legacy observed_average_krw remains Lowchart-only; also expose
        # Fallcent's separately confirmed average, never a midpoint.
        "observed_average_krw": (
            average if reference_source == "lowchart_public_history" else None
        ),
        "fallcent_observed_average_krw": (
            average if reference_source == "fallcent_observed_average" else None
        ),
        "observed_average_source": (
            reference_source if average is not None and not nonaverage_reference
            else None
        ),
        "comparison_reference_price_krw": average,
        "comparison_reference_source": reference_source if average is not None else None,
        "comparison_price_is_historical_average": not nonaverage_reference and average is not None,
        "rounded_discount_step": DEFAULT_PERCENT_STEP,
        "approx_discount_vs_reference_pct": approx,
        "approx_discount_vs_historical_mean_pct": (
            approx if not nonaverage_reference else None
        ),
        "actual_discount_vs_historical_mean_pct": (
            round((average - sale) / average * 100, 1)
            if approx is not None and not nonaverage_reference else None
        ),
        "actual_discount_vs_reference_pct": (
            round((average - sale) / average * 100, 1)
            if approx is not None else None
        ),
        "official_normal_price_quoted": False,
        # Two Enter presses between fields: one blank paragraph on mobile.
        "caption": "\n\n".join(lines),
        "auto_publish_ready": False,
    }
