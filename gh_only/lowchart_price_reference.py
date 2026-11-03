"""Reusable, read-only Lowchart historical-price reference for one Coupang item.

Only public human-visible summary fields from an exact productId-itemId page.
Not the official Coupang 'normal/list price'; not a 30/60-day median; no hidden
APIs. No automatic posting. Any mismatch/missing observation fails closed.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib import request, error
from urllib.parse import urlsplit
from gh_only.price_history_probe import HTMLInfo

# Legacy public summary format. Keep it for archived/product pages that still
# display the older full-history observation sequence.
PRICE_SUMMARY = re.compile(
    r"현재가\s*([\d,]+)원\s+관측\s*최저가\s*([\d,]+)원\s+"
    r"관측\s*최고가\s*([\d,]+)원\s+관측\s*평균가\s*([\d,]+)원\s+"
    r"최근\s*변동일\s*(\d{4}-\d{2}-\d{2})"
)
# Current Lowchart pages show a natural-language per-item summary such as:
# "<item>의 쿠팡 현재가는 15,300원이며, 로우차트가 관측한 역대
# 최저가는 12,600원, 최고가는 15,840원입니다.
# 최근 3주 평균 15,460원보다 1% 저렴합니다."
# The *recent 3-week average* is distinct from any lifetime/graph mean.
CURRENT_VISIBLE_SUMMARY = re.compile(
    r"의\s+쿠팡\s+현재가는\s*([\d,]+)\s*원이며,?\s*"
    r"로우차트가\s+관측한\s+역대\s+최저가는\s*([\d,]+)\s*원,?\s*"
    r"최고가는\s*([\d,]+)\s*원입니다\.?\s*"
    r"최근\s*(\d{1,2})\s*주\s*평균\s*([\d,]+)\s*원"
)
TITLE_PRICE_SUFFIX = re.compile(r"\s+[-–]\s+[\d,]+\s*원\s+[-–]\s+로우차트\b")
MAX_BODY = 1_800_000
TARGET = ("6221369038", "12453073752", "오뚜기", "핫케이크", "1kg", "1개", 3180)


def build_url(product_id: str, item_id: str) -> str:
    if not (re.fullmatch(r"\d{5,18}", product_id)
            and re.fullmatch(r"\d{5,18}", item_id)):
        raise ValueError("INVALID_PRODUCT_OR_ITEM_ID")
    return f"https://www.lowchart.com/{product_id}-{item_id}"


def parse_reference(html: str, source_url: str, product_id: str, item_id: str,
                    expected_tokens: tuple[str, ...], deal_price: int) -> dict:
    if source_url != build_url(product_id, item_id):
        raise ValueError("SOURCE_URL_NOT_EXACT_ITEM")
    if type(deal_price) is not int or not 1 <= deal_price <= 100_000_000:
        raise ValueError("INVALID_DEAL_PRICE")
    parser = HTMLInfo()
    parser.feed(html)
    title = parser.title.strip()
    text = " ".join(" ".join(parser.text_chunks).split())
    result = {
        "provider": "lowchart", "source_url": source_url,
        "product_id": product_id, "item_id": item_id,
        "page_title": title[:160],
        "source_observation_type": "price_history_summary",
        "history_window": "entire_tracked_history_unspecified_days",
        "observed_low_krw": None, "observed_high_krw": None,
        "observed_average_krw": None, "observed_current_krw": None,
        "source_last_change_date": None,
        "reference_discount_vs_average_pct": None,
        "source_item_identifiers_present": product_id in html and item_id in html,
        "title_option_matches": all(token.lower() in title.lower()
                                    for token in expected_tokens),
        "usable_historical_reference": False,
        "official_normal_price": False,
        "reason": "UNVERIFIED",
    }
    if not result["source_item_identifiers_present"]:
        result["reason"] = "MISSING_EXACT_IDS_IN_SOURCE"
        return result
    if not result["title_option_matches"]:
        result["reason"] = "OPTION_TITLE_MISMATCH"
        return result
    # Prefer the currently displayed, explicitly scoped *recent-week* mean.
    # Pin the narrative to the actual product name from this exact-item page,
    # so a recommendation or a different SKU cannot supply another mean.
    product_name = TITLE_PRICE_SUFFIX.split(title, maxsplit=1)[0].strip()
    current_summary = None
    if product_name and product_name != title:
        current_summary = re.search(
            re.escape(product_name) + CURRENT_VISIBLE_SUMMARY.pattern, text
        )
    if current_summary:
        current, low, high, weeks, avg = (
            int(value.replace(",", "")) for value in current_summary.groups()
        )
        if not 1 <= weeks <= 52:
            result["reason"] = "INVALID_RECENT_AVERAGE_WINDOW"
            return result
        window = f"recent_{weeks}_weeks"
        observation = "price_history_recent_week_summary"
        reason = "EXACT_ITEM_VISIBLE_RECENT_AVERAGE_MATCH"
        date = None  # The new summary does not expose a YYYY-MM-DD change date.
    else:
        # Support old pages without conflating their all-history mean with
        # Lowchart's new 3-week mean.
        legacy = PRICE_SUMMARY.search(text)
        if not legacy:
            result["reason"] = "VISIBLE_HISTORY_SUMMARY_NOT_FOUND"
            return result
        current, low, high, avg = (
            int(value.replace(",", "")) for value in legacy.groups()[:4]
        )
        window = "entire_tracked_history_unspecified_days"
        observation = "price_history_summary"
        reason = "EXACT_ITEM_ROUTE_VISIBLE_STATS_MATCH"
        date = legacy.group(5)
    if not (0 < low <= avg <= high < 100_000_000 and low <= current <= high):
        result["reason"] = "INVALID_OBSERVED_PRICE_ORDER"
        return result
    result.update({
        "observed_current_krw": current, "observed_low_krw": low,
        "observed_high_krw": high, "observed_average_krw": avg,
        "history_window": window,
        "source_observation_type": observation,
        "source_last_change_date": date,
        "reference_discount_vs_average_pct": round((avg-deal_price)*100/avg,1),
        "usable_historical_reference": True,
        "reason": reason,
    })
    return result


def fetch_one(product_id: str, item_id: str, expected_tokens: tuple[str, ...],
              deal_price: int, opener=None) -> dict:
    url = build_url(product_id, item_id)
    fetch = opener or request.urlopen
    req = request.Request(url, headers={
        "Accept": "text/html",
        "User-Agent": "GitHubHotdealHistoryOneShotDiagnostic/1.0",
    })
    try:
        with fetch(req, timeout=20) as response:
            status=response.status
            host=urlsplit(response.geturl()).hostname
            body=response.read(MAX_BODY + 1)
    except error.HTTPError as exc:
        return {"provider": "lowchart", "http_status": exc.code,
                "reason": "HTTP_ERROR", "usable_historical_reference": False}
    except Exception as exc:
        return {"provider": "lowchart", "reason": "NETWORK_ERROR",
                "error_type": type(exc).__name__, "usable_historical_reference": False}
    if status != 200 or host not in ("lowchart.com", "www.lowchart.com") or len(body)>MAX_BODY:
        return {"provider": "lowchart", "http_status":status,
                "reason":"UNEXPECTED_RESPONSE", "usable_historical_reference":False}
    return {"http_status": status,
            **parse_reference(body.decode("utf-8","replace"),url,product_id,item_id,
                              expected_tokens,deal_price)}


if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",default="lowchart_price_reference.json")
    a=ap.parse_args()
    p,i,*rest=TARGET
    brand,kind,weight,count,price=rest
    obj=fetch_one(p,i,(brand,kind,weight,count),price)
    obj["schema"]="github-jirum-lowchart-price-reference.v1"
    obj["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    obj["deal_price_krw"]=price
    obj["posts_published"]=0
    Path(a.out).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("LOWCHART_PRICE_REFERENCE "+json.dumps(obj,ensure_ascii=False))
