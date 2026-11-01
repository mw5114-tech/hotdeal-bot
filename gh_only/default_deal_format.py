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


def same_named_option(og_title: str | None, history_title: str | None) -> bool:
    """Strict title/option match. Reject similar product in different package sizes."""
    left, right = normalized_product_name(og_title), normalized_product_name(history_title)
    return len(left) >= 9 and left == right


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
                           average: int | None, source_id: int | None = None) -> dict:
    """Output only the user's chosen default copy; no fabricated source prices."""
    sale = sale if type(sale) is int and 0 < sale <= 100_000_000 else None
    average = average if type(average) is int and 0 < average <= 100_000_000 else None
    approx = nearest_five_discount(sale, average)
    # Requested three-line layout. The reference MUST remain clearly labeled
    # as Lowchart's observed historical average, not an official Coupang list price.
    product = title.strip()[:150] if isinstance(title, str) and title.strip() else "상품명 확인 중"
    lines = [f"📦 상품명 : {product}"]
    if average is not None:
        lines.append(f"💸 쿠팡 평소 판매가(로우차트 관측 평균) : 약 {average:,}원")
    else:
        lines.append("💸 쿠팡 평소 판매가(로우차트 관측 평균) : 확인 중")
    if sale is not None:
        price_text = f"🔥 핫딜 특가 : {sale:,}원"
        if approx is not None:
            price_text += f" (상세페이지 참고 / 약 {approx}% 혜택)"
        else:
            price_text += " (상세페이지 참고)"
    else:
        price_text = "🔥 핫딜 특가 : 확인 중"
    lines.append(price_text)
    return {
        "source_message_id": source_id,
        "sale_price_krw": sale,
        "sale_price_source": "telegram_jirum_message",
        "observed_average_krw": average,
        "observed_average_source": "lowchart_public_history" if average is not None else None,
        "rounded_discount_step": DEFAULT_PERCENT_STEP,
        "approx_discount_vs_historical_mean_pct": approx,
        "actual_discount_vs_historical_mean_pct": (
            round((average - sale) / average * 100, 1)
            if approx is not None else None
        ),
        "official_normal_price_quoted": False,
        "caption": "\n".join(lines),
        "auto_publish_ready": False,
    }
