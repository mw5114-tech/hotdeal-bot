"""One-shot public historical-price evidence probe for the Jirum hotcake deal.

No Coupang detail visits, browser login, third-party hidden APIs, proxy/evasion,
affiliate operations, or publishing. One GET per allowlisted public page.
This checks accessibility and whether the exact product AND item identities
can be tied to historical prices; product-name similarity alone is NOT proof.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import json
import re
from urllib import request, error
from urllib.parse import urlsplit, parse_qs

PRODUCT_ID = "6221369038"
ITEM_ID = "12453073752"
TARGET_TITLE = "오뚜기 핫케이크 믹스 1kg 1개"
TARGET_POST_PRICE = 3180
SOURCE_URLS = (
    ("lowchart", "https://www.lowchart.com/6221369038-12453073752", "ID_TEMPLATE_UNCONFIRMED"),
    ("jigeumsa", "https://jigeumsa.com/", "HOME_ONLY_NO_ITEM_SEARCH"),
    ("hotduk", "https://hotduk.com/deal/156817", "KNOWN_HOTDEAL_PAGE"),
)
BYTES_LIMIT = 1_800_000
DAY_HISTORY = re.compile(
    r"최근\s*(\d{1,4})일\s*동안\s*이\s*상품은\s*"
    r"([\d,]+)원\s*[~～\-]\s*([\d,]+)원\s*사이였고[,\s]*"
    r"평균은\s*([\d,]+)원",
)
BLOCK_TEXT = ("사용권한이 없습니다", "사용권한이 제한된 페이지", "access denied",
              "enable javascript to continue", "captcha")


class HTMLInfo(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags_to_ignore = 0
        self.title = ""
        self.in_title = False
        self.text_chunks = []
        self.metas = {}
        self.product_links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.tags_to_ignore += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = attrs.get("property", attrs.get("name"))
            if key in ("og:title", "og:description"):
                self.metas[key] = attrs.get("content", "")[:180]
        if tag == "a":
            link = attrs.get("href", "")
            if isinstance(link, str) and "coupang.com" in link:
                self.product_links.append(link[:1800])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("script", "style"):
            self.tags_to_ignore = max(0, self.tags_to_ignore - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data[:300]
        if not self.tags_to_ignore:
            self.text_chunks.append(data)


def url_ids_match(link: str) -> bool:
    """Only exact Coupang path+itemId, not a matching product name or different SKU."""
    try:
        u = urlsplit(link)
        return (u.scheme == "https" and u.hostname in ("www.coupang.com", "coupang.com")
                and u.username is None and u.password is None
                and u.path.rstrip("/") == "/vp/products/" + PRODUCT_ID
                and parse_qs(u.query).get("itemId") == [ITEM_ID])
    except ValueError:
        return False


def parse_history(html: str, provider: str, source_type: str) -> dict:
    parser = HTMLInfo()
    parser.feed(html)
    visible = " ".join(" ".join(parser.text_chunks).split())
    title = parser.metas.get("og:title") or parser.title
    exact_links = [x for x in parser.product_links if url_ids_match(x)]
    case = {
        "provider": provider,
        "probe_type": source_type,
        "page_title": title[:160],
        "source_option_link_match": bool(exact_links),
        "source_item_id_verified": bool(exact_links),
        "product_name_mentioned": "핫케이크" in visible and "오뚜기" in visible,
        "historical_average_krw": None,
        "historical_min_krw": None,
        "historical_max_krw": None,
        "historical_days": None,
        "history_found": False,
        "normal_price_verified_for_target_item": False,
        "reason": "NOT_FOUND_IN_VISIBLE_HTML",
    }
    if provider == "hotduk":
        m = DAY_HISTORY.search(visible)
        if m:
            days, low, high, avg = (int(x.replace(",", "")) for x in m.groups())
            if 1 <= days <= 3650 and 0 < low <= avg <= high < 100000000:
                case.update({
                    "historical_days": days,
                    "historical_min_krw": low,
                    "historical_max_krw": high,
                    "historical_average_krw": avg,
                    "history_found": True,
                    "reason": ("HISTORY_AVAILABLE_EXACT_OPTION_LINK_VERIFIED" if exact_links
                               else "HISTORY_VISIBLE_ITEM_ID_NOT_VERIFIED"),
                })
    if provider == "jigeumsa":
        case["reason"] = "HOMEPAGE_ONLY_URL_SEARCH_NOT_PERFORMED"
    if provider == "lowchart" and source_type == "ID_TEMPLATE_UNCONFIRMED":
        case["reason"] = "SPECULATIVE_PRODUCT_ROUTE_NO_VERIFIED_ITEM_MATCH"
    if any(marker in visible.lower() for marker in BLOCK_TEXT):
        case["reason"] = "ACCESS_BLOCKED_OR_CHALLENGED"
    return case


def probe(provider: str, url: str, source_type: str, opener=None) -> dict:
    allowed = {"lowchart": {"www.lowchart.com", "lowchart.com"},
               "jigeumsa": {"jigeumsa.com", "www.jigeumsa.com"},
               "hotduk": {"hotduk.com", "www.hotduk.com"}}
    host = urlsplit(url).hostname
    if host not in allowed.get(provider, set()):
        raise ValueError("SOURCE_DOMAIN_NOT_ALLOWLISTED")
    fetch = opener or request.urlopen
    req = request.Request(url, headers={
        "Accept": "text/html",
        "User-Agent": "GitHubHotdealHistoryOneShotDiagnostic/1.0",
    })
    try:
        with fetch(req, timeout=20) as response:
            status = response.status
            final_host = urlsplit(response.geturl()).hostname
            body = response.read(BYTES_LIMIT + 1)
    except error.HTTPError as exc:
        return {"provider": provider, "probe_type": source_type,
                "http_status": exc.code, "result": "HTTP_ERROR",
                "history_found": False, "source_item_id_verified": False}
    except Exception as exc:
        return {"provider": provider, "probe_type": source_type,
                "result": "NETWORK_ERROR", "error_type": type(exc).__name__,
                "history_found": False, "source_item_id_verified": False}
    if status != 200 or final_host not in allowed[provider] or len(body) > BYTES_LIMIT:
        return {"provider": provider, "probe_type": source_type,
                "http_status": status, "result": "UNEXPECTED_RESPONSE",
                "history_found": False, "source_item_id_verified": False}
    found = parse_history(body.decode("utf-8", "replace"), provider, source_type)
    return {"http_status": status, "result": "PAGE_PARSED", **found}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="jirim_price_history_probe.json")
    args = p.parse_args()
    results = [probe(*target) for target in SOURCE_URLS]
    report = {
        "schema": "github-jirum-price-history-probe.v1",
        "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_id": PRODUCT_ID,
        "item_id": ITEM_ID,
        "post_price_krw": TARGET_POST_PRICE,
        "queries": len(results),
        "no_paid_api": True,
        "no_coupang_product_page_request": True,
        "no_publish": True,
        "sources": results,
        "confirmed_exact_option_history_sources": sum(bool(r.get("history_found"))
                   and bool(r.get("source_item_id_verified")) for r in results),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    print("HISTORY_PRICE_PROBE " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
