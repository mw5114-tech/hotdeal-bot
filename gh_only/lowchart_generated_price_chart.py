"""Generate our OWN price-comparison graphic from verified Lowchart summaries.

Lowchart does not publicly expose its timestamped price series. This module
never invents a time-series curve and never copies/recolors its chart image.
It plots ONLY verified summary values for the exact Coupang product+item,
and the Telegram-sourced deal price, as an accurately labeled range graphic.
The optional image must not block a valid affiliate post on failure.
"""
from __future__ import annotations

from html import escape
import os
from pathlib import Path
import re
from typing import Any

from gh_only.lowchart_chart_capture import _exact_identity
from gh_only.lowchart_price_reference import build_url

MAX_IMAGE_BYTES = 2_500_000
MAX_PRICE = 100_000_000


def _price(value: Any) -> int | None:
    return value if type(value) is int and 0 < value <= MAX_PRICE else None


def _model(identity: dict | None) -> dict | None:
    """Validate all provenance and graph positions; no guesswork or repair."""
    ids = _exact_identity(identity)
    if ids is None or not isinstance(identity, dict):
        return None
    product_id, item_id = ids
    if identity.get("source_url") != build_url(*ids):
        return None
    if identity.get("verified_exact_item") is not True:
        return None
    basis = identity.get("comparison_reference_source")
    allowed = {
        "lowchart_public_history": "OBSERVED AVERAGE",
        "lowchart_observed_high": "OBSERVED HIGH",
        "lowchart_observed_current": "OBSERVED CURRENT",
        "lowchart_observed_low": "OBSERVED LOW",
    }
    if basis not in allowed:
        return None
    low = _price(identity.get("observed_low_krw"))
    high = _price(identity.get("observed_high_krw"))
    avg = _price(identity.get("observed_average_krw"))
    current = _price(identity.get("observed_current_krw"))
    reference = _price(identity.get("comparison_reference_price_krw"))
    deal = _price(identity.get("deal_price_krw"))
    if (None in (low, high, current, reference, deal)
            or low > high or not (low <= current <= high)
            or (avg is not None and not (low <= avg <= high))):
        return None
    expected = {
        "lowchart_public_history": avg,
        "lowchart_observed_high": high,
        "lowchart_observed_current": current,
        "lowchart_observed_low": low,
    }[basis]
    if expected is None or reference != expected:
        return None
    window = identity.get("history_window")
    if not isinstance(window, str) or len(window) > 70:
        return None
    if basis == "lowchart_public_history":
        week_match = re.fullmatch(r"recent_(\d{1,2})_weeks", window)
        if week_match and 1 <= int(week_match.group(1)) <= 52:
            average_caption = "RECENT " + week_match.group(1) + "-WEEK AVG"
        elif window == "entire_tracked_history_unspecified_days":
            average_caption = "OBSERVED AVG"
        else:
            return None
    else:
        average_caption = "OBSERVED AVG" if avg is not None else None
    return {
        "product_id": product_id, "item_id": item_id,
        "low": low, "high": high, "current": current, "avg": avg,
        "reference": reference, "deal": deal,
        "reference_caption": allowed[basis],
        "average_caption": average_caption, "source_url": identity["source_url"],
    }


def _money(value: int) -> str:
    return f"{value:,} KRW"


def _svg(model: dict) -> str:
    """Deterministic lightweight 1120x610 SVG with no third-party assets."""
    width, height = 1120, 610
    low, high, deal, avg = (model[k] for k in ("low", "high", "deal", "avg"))
    prices = [low, high, deal]
    if avg is not None:
        prices.append(avg)
    plot_min, plot_max = min(prices), max(prices)
    padding = max(800, round((plot_max - plot_min) * 0.12))
    axis_min = max(0, plot_min - padding)
    axis_max = plot_max + padding
    left, right = 106, 1023
    def x(value: int) -> int:
        return int(left + (value - axis_min) / (axis_max - axis_min) * (right - left))

    # Marker colors: Lowchart observations navy/blue; Telegram deal teal.
    # No date labels or trend line: there is no public raw timestamp series.
    markup = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1120" height="610" fill="#FFFFFF"/>',
        '<rect x="1" y="1" width="1118" height="608" rx="27" fill="none" stroke="#DFE6EE" stroke-width="2"/>',
        '<rect x="44" y="43" width="10" height="62" rx="5" fill="#176BCE"/>',
        '<text x="72" y="71" font-size="32" font-weight="750" fill="#16283E" font-family="Arial,DejaVu Sans,sans-serif">PRICE COMPARISON</text>',
        '<text x="72" y="101" font-size="15" fill="#627389" font-family="Arial,DejaVu Sans,sans-serif">LOWCHART VERIFIED SUMMARY  |  EXACT COUPANG ITEM</text>',
        '<rect x="44" y="133" width="1032" height="117" rx="19" fill="#F4F8FD"/>',
    ]
    cards = [
        ("OBSERVED LOW", low, 73, "#1D5798"),
        (model["average_caption"] or "OBSERVED CURRENT", avg if avg is not None else model["current"], 338, "#1D5798"),
        ("OBSERVED HIGH", high, 603, "#1D5798"),
        ("HOTDEAL SOURCE", deal, 862, "#087F6A"),
    ]
    for name, value, cx, color in cards:
        markup.append(f'<text x="{cx}" y="174" font-size="14" fill="#54677E" font-family="Arial,DejaVu Sans,sans-serif">{escape(name)}</text>')
        markup.append(f'<text x="{cx}" y="216" font-size="25" font-weight="740" fill="{color}" font-family="Arial,DejaVu Sans,sans-serif">{_money(value)}</text>')
    for cx in (318, 583, 838):
        markup.append(f'<line x1="{cx}" y1="153" x2="{cx}" y2="229" stroke="#DCE5F0" stroke-width="2"/>')

    markup.extend([
        '<text x="72" y="303" font-size="21" font-weight="700" fill="#24384C" font-family="Arial,DejaVu Sans,sans-serif">Observed price range</text>',
        '<text x="72" y="330" font-size="15" fill="#6E8094" font-family="Arial,DejaVu Sans,sans-serif">Price position only - not a timeline</text>',
        '<line x1="106" y1="396" x2="1023" y2="396" stroke="#DCE6EF" stroke-width="13" stroke-linecap="round"/>',
        f'<line x1="{x(low)}" y1="396" x2="{x(high)}" y2="396" stroke="#5487D8" stroke-width="13" stroke-linecap="round"/>',
    ])
    for value, label, color, title_y, below in (
        (low, "LOW", "#176BCE", 374, False),
        (high, "HIGH", "#176BCE", 374, False),
        (deal, "DEAL", "#087F6A", 442, True),
    ):
        xpos = x(value)
        markup.append(f'<circle cx="{xpos}" cy="396" r="12" fill="{color}" stroke="#FFFFFF" stroke-width="4"/>')
        markup.append(f'<text x="{xpos}" y="{title_y}" font-size="15" font-weight="700" text-anchor="middle" fill="{color}" font-family="Arial,DejaVu Sans,sans-serif">{label}</text>')
    if avg is not None:
        avg_x = x(avg)
        markup.append(f'<line x1="{avg_x}" y1="348" x2="{avg_x}" y2="410" stroke="#E99B27" stroke-width="3" stroke-dasharray="6 6"/>')
        markup.append(f'<circle cx="{avg_x}" cy="396" r="9" fill="#E99B27" stroke="#FFFFFF" stroke-width="3"/>')
    delta = round((model["reference"] - deal) * 100 / model["reference"], 1)
    delta_word = "BELOW" if delta > 0 else "ABOVE" if delta < 0 else "AT"
    markup.extend([
        '<rect x="44" y="478" width="1032" height="69" rx="15" fill="#EAF7F2"/>',
        f'<text x="72" y="520" font-size="23" font-weight="750" fill="#08735F" font-family="Arial,DejaVu Sans,sans-serif">DEAL IS {abs(delta):g}% {delta_word} {escape(model["reference_caption"])}</text>',
        '<text x="65" y="575" font-size="14" fill="#617185" font-family="Arial,DejaVu Sans,sans-serif">Lowchart observed prices may lag actual store prices. Deal price is Telegram-reported, not live verified.</text>',
        '</svg>',
    ])
    return "".join(markup)


def generate_price_chart_image(browser, identity: dict | None,
                               folder: Path, source_id: int) -> Path | None:
    """Draw self-authored SVG from verified numbers and export PNG via Chromium.

    No Lowchart screenshot or hidden API. Failure returns None and caller keeps
    the existing normal post format; never remove the affiliate disclosure.
    """
    if os.getenv("JIRUM_LOWCHART_CHART_MODE") != "generated":
        return None
    model = _model(identity)
    if model is None or type(source_id) is not int or source_id <= 0:
        print("LOWCHART_GENERATED_CHART_SKIPPED_UNVERIFIED_PRICE_DATA")
        return None
    context = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"jirum_{source_id}_generated_price_chart.png"
        context = browser.new_context(viewport={"width": 1150, "height": 680},
                                      device_scale_factor=1)
        page = context.new_page()
        page.set_content("<!doctype html><html><body style='margin:0'>"
                         + _svg(model) + "</body></html>", wait_until="load")
        page.locator("svg").screenshot(path=str(output),type="png",timeout=10000)
        if not (7000 <= output.stat().st_size <= MAX_IMAGE_BYTES):
            output.unlink(missing_ok=True)
            print("LOWCHART_GENERATED_CHART_SKIPPED_INVALID_IMAGE")
            return None
        print("LOWCHART_GENERATED_CHART_OK " + str({
            "source_id": source_id, "product_id": model["product_id"],
            "item_id": model["item_id"], "bytes": output.stat().st_size,
            "image_type": "verified_price_range_not_time_series",
        }))
        return output
    except Exception as exc:
        print("LOWCHART_GENERATED_CHART_SKIPPED_" + type(exc).__name__.upper())
        return None
    finally:
        if context is not None:
            context.close()
