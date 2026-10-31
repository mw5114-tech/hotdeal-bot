"""One direct, no-login Coupang product-page access diagnostic in GitHub Actions.

No retry, proxy, browser spoofing, login, affiliate changes, posting, or scheduling.
Reports access status and a few generic HTML signals, never assumes cached
search-engine prices are fresh merchant quotes.
"""
from __future__ import annotations
import json
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib import request, error
from urllib.parse import urlsplit

URL = "https://www.coupang.com/vp/products/6221369038?itemId=12453073752"
MAX_BODY = 1_500_000
BLOCK_MARKERS = ("사용권한이 없습니다", "사용권한이 제한된", "접근이 제한", "access denied", "forbidden")

class PageHead(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""
        self.metas = {}
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key in ("og:title","og:description","product:price:amount","product:price:currency"):
                self.metas[key] = str(a.get("content",""))[:180]
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
    def handle_data(self, data):
        if self.in_title:
            self.title += data[:200]

def diagnose():
    req = request.Request(URL, headers={"Accept": "text/html"})
    try:
        with request.urlopen(req, timeout=22) as response:
            status = response.status
            host = urlsplit(response.geturl()).hostname
            body = response.read(MAX_BODY + 1)
    except error.HTTPError as exc:
        return {"status": "HTTP_ERROR", "http_code": exc.code, "live_product_price_verified": False}
    except Exception as exc:
        return {"status":"NETWORK_ERROR", "error_type":type(exc).__name__, "live_product_price_verified":False}
    if host not in ("www.coupang.com","coupang.com") or len(body) > MAX_BODY:
        return {"status":"UNEXPECTED_RESPONSE", "http_code":status, "live_product_price_verified":False}
    html = body.decode("utf-8", "replace")
    blocked = any(x in html.lower() for x in BLOCK_MARKERS)
    p = PageHead()
    p.feed(html)
    metas = p.metas
    return {
        "status": "DENIED_PAGE" if blocked else ("PRODUCT_HTML_RECEIVED" if status == 200 else "OTHER_STATUS"),
        "http_code": status,
        "page_title": p.title[:140],
        "og_title": metas.get("og:title"),
        "og_price_amount": metas.get("product:price:amount"),
        "og_price_currency": metas.get("product:price:currency"),
        "contains_product_name": "핫케이크" in html,
        "body_bytes": len(body),
        "live_product_price_verified": False, # metadata alone is insufficient
    }

if __name__ == "__main__":
    result = {
        "schema":"gh-coupang-live-url-single-probe.v1",
        "target_product_id":"6221369038",
        "target_item_id":"12453073752",
        "requests_max":1,
        "no_login":True,
        "no_retry":True,
        "published":False,
        **diagnose()
    }
    Path("coupang_live_price_probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("COUPANG_LIVE_PRICE_PROBE "+json.dumps(result,ensure_ascii=False))
