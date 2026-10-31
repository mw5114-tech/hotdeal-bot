"""Read-only single Bing image-search diagnostic for an EXACT Coupang product+item ID.

No Coupang page visits, proxies, CAPTCHA handling, account logins or posting.
Only publicly indexed image metadata are considered; NEVER treat an image as
verified exact-option media just because a search result appears.
"""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urlsplit
from urllib import request, error
import json
from pathlib import Path

PRODUCT_ID = "6221369038"
ITEM_ID = "12453073752"
TITLE = "오뚜기 핫케이크 믹스 1kg 1개"
MAX_HTML = 1_200_000
ALLOWED_IMAGE_HOSTS = (
    "thumbnail.coupangcdn.com", "image.coupangcdn.com",
    "static.megamart.com", "img.danawa.com",
)

def safe_url(raw):
    if not isinstance(raw, str) or len(raw) > 2500:
        return None
    try:
        url = urlsplit(raw)
        if (url.scheme != "https" or not url.hostname or
                url.username or url.password or url.port not in (None, 443)):
            return None
        return raw
    except ValueError:
        return None

def source_identity(source_url):
    """Only source URLs that literally contain both IDs count as an exact URL match."""
    good = safe_url(source_url)
    if not good:
        return "NO_SOURCE_URL"
    parts = urlsplit(good)
    if parts.hostname in ("www.coupang.com", "coupang.com"):
        if parts.path == "/vp/products/" + PRODUCT_ID and parse_qs(parts.query).get("itemId") == [ITEM_ID]:
            return "SOURCE_URL_MATCHES_PRODUCT_AND_ITEM"
        if parts.path == "/vp/products/" + PRODUCT_ID:
            return "SOURCE_URL_PRODUCT_ONLY"
    return "OTHER_SOURCE_URL"

class BingImageResults(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.candidates=[]
        self.indexed_cards=0

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a=dict(attrs)
        if not ({"iusc", "richImgLnk"} & set(a.get("class", "").split()) or "m" in a and "murl" in a.get("m","")):
            return
        self.indexed_cards += 1
        raw=a.get("m","")
        if not raw:
            return
        try:
            obj=json.loads(raw)
        except (ValueError, TypeError):
            return
        img=safe_url(obj.get("murl"))
        page=safe_url(obj.get("purl"))
        if not img or not page:
            return
        kind=source_identity(page)
        self.candidates.append({
            "source_identity":kind,
            "image_host": urlsplit(img).hostname,
            "source_host": urlsplit(page).hostname,
            "image_url_candidate":img if kind == "SOURCE_URL_MATCHES_PRODUCT_AND_ITEM" else None,
            "source_page":page if kind == "SOURCE_URL_MATCHES_PRODUCT_AND_ITEM" else None,
            "status":"INDEXED_IMAGE_UNVERIFIED" if kind == "SOURCE_URL_MATCHES_PRODUCT_AND_ITEM" else "SOURCE_ITEM_ID_NOT_MATCHED",
        })

def parse_bing_image_html(html):
    p=BingImageResults()
    p.feed(html)
    return {
        "cards_seen":p.indexed_cards,
        "candidates_with_source":len(p.candidates),
        "matching_product_and_item":[x for x in p.candidates if x["source_identity"]=="SOURCE_URL_MATCHES_PRODUCT_AND_ITEM"][:3],
        "product_only_count":sum(x["source_identity"]=="SOURCE_URL_PRODUCT_ONLY" for x in p.candidates),
        "other_count":sum(x["source_identity"] in ("OTHER_SOURCE_URL","NO_SOURCE_URL") for x in p.candidates),
        "exact_photo_content_verified":False,
    }

def fetch_once(query_mode="exact"):
    q=(f'"{PRODUCT_ID}" "{ITEM_ID}" {TITLE}' if query_mode=="exact"
       else '오뚜기 핫케이크 믹스 1kg 1개')
    url="https://www.bing.com/images/search?q="+quote_plus(q)
    req=request.Request(url,headers={
        "User-Agent":"Mozilla/5.0",
        "Accept":"text/html",
    })
    try:
        with request.urlopen(req,timeout=22) as response:
            status=response.status
            final_host=urlsplit(response.geturl()).hostname
            body=response.read(MAX_HTML+1)
    except error.HTTPError as e:
        return {"fetch_status":e.code, "status":"SEARCH_ACCESS_DENIED",
                "result":None}
    except Exception as e:
        return {"fetch_status":None, "status":"SEARCH_NETWORK_ERROR",
                "error_class":type(e).__name__, "result":None}
    if status != 200 or final_host not in ("www.bing.com","bing.com") or len(body)>MAX_HTML:
        return {"fetch_status":status,"status":"SEARCH_UNEXPECTED_RESPONSE","result":None}
    decoded=body.decode("utf-8","replace")
    found=parse_bing_image_html(decoded)
    found["markup_diagnostic"]={"murl_occurrences":decoded.count("murl"),
                                 "iusc_occurrences":decoded.count("iusc"),
                                 "robot_challenge_detected":("captcha" in decoded.lower() or
                                      "unusual traffic" in decoded.lower())}
    return {"fetch_status":status,"status":"SEARCH_PARSED","result":found}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture-html")
    parser.add_argument("--query-mode",choices=("exact","relaxed"),default="exact")
    parser.add_argument("--output",default="indexed_image_probe.json")
    args=parser.parse_args()
    if args.fixture_html:
        html=Path(args.fixture_html).read_text("utf-8")
        report={"status":"FIXTURE_PARSED","result":parse_bing_image_html(html)}
    else:
        report=fetch_once(args.query_mode)
    out={
        "schema":"github-only-indexed-image-probe.v1",
        "target_product_id":PRODUCT_ID,
        "target_item_id":ITEM_ID,
        "query_mode":args.query_mode,
        "merchant_price_queried":False,
        "image_authorized_for_publication":False,
        **report
    }
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("INDEXED_IMAGE_PROBE "+json.dumps({
        "status":out["status"],
        "fetch_status":out.get("fetch_status"),
        "cards":out.get("result",{}).get("cards_seen") if out.get("result") else 0,
        "matching_exact_source_ids":len(out["result"]["matching_product_and_item"]) if out.get("result") else 0,
        "product_only":out.get("result",{}).get("product_only_count") if out.get("result") else 0,
        "markup":out.get("result",{}).get("markup_diagnostic") if out.get("result") else None
    },ensure_ascii=False))

if __name__=="__main__":
    main()
