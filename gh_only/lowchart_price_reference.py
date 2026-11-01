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

PRICE_SUMMARY = re.compile(
    r"현재가\s*([\d,]+)원\s+관측\s*최저가\s*([\d,]+)원\s+"
    r"관측\s*최고가\s*([\d,]+)원\s+관측\s*평균가\s*([\d,]+)원\s+"
    r"최근\s*변동일\s*(\d{4}-\d{2}-\d{2})"
)
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
    m = PRICE_SUMMARY.search(text)
    if not m:
        result["reason"] = "VISIBLE_HISTORY_SUMMARY_NOT_FOUND"
        return result
    current, low, high, avg = (int(value.replace(",", "")) for value in m.groups()[:4])
    date = m.group(5)
    if not (0 < low <= avg <= high < 100_000_000 and low <= current <= high):
        result["reason"] = "INVALID_OBSERVED_PRICE_ORDER"
        return result
    result.update({
        "observed_current_krw": current, "observed_low_krw": low,
        "observed_high_krw": high, "observed_average_krw": avg,
        "source_last_change_date": date,
        "reference_discount_vs_average_pct": round((avg-deal_price)*100/avg,1),
        "usable_historical_reference": True,
        "reason": "EXACT_ITEM_ROUTE_VISIBLE_STATS_MATCH",
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
