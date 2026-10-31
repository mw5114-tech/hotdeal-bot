"""One Google public-index diagnostics run, without visiting Coupang details.

Do not infer the exact item photo from an unrelated image card. It is NOT
a production scraper and has no retries, sessions, captcha handling or proxies.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from urllib import request,error
from urllib.parse import quote_plus,urlsplit
from html import unescape

PATTERN = re.compile(r'https?[:\\/]{1,5}(?:thumbnail|image)\.coupangcdn\.com[^\s"<>]{6,300}',re.I)
MAX_BODY=1400000
QUERY='"6221369038" "12453073752" 핫케이크 믹스'

def parse_index_metadata(data: str) -> dict:
    cooked=unescape(data.replace(r"\/","/").replace(r"\u003d","=").replace(r"\u0026","&"))
    urls=set()
    for match in PATTERN.finditer(cooked):
        candidate=match.group(0).rstrip(".,);\\")
        candidate=candidate.replace("\\/","/")
        p=urlsplit(candidate)
        if p.scheme=="https" and p.hostname in ("thumbnail.coupangcdn.com","image.coupangcdn.com"):
            urls.add(candidate)
    return {"coupang_cdn_url_candidates":sorted(urls)[:8],
            "indexed_image_host_count":len(urls),
            "captcha_markers_present":any(s in cooked.lower() for s in ("captcha","unusual traffic","sorry/index")),
            "found_target_product_id": "6221369038" in cooked,
            "found_target_item_id": "12453073752" in cooked,
            "exact_image_item_match_verified": False}

def fetch():
    url="https://www.google.com/search?tbm=isch&q="+quote_plus(QUERY)
    req=request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"text/html"})
    try:
        with request.urlopen(req,timeout=20) as response:
            status=response.status
            hostname=urlsplit(response.geturl()).hostname
            body=response.read(MAX_BODY+1)
    except error.HTTPError as exc:
        return {"state":"HTTP_ERROR","http_status":exc.code,"diagnostic":None}
    except Exception as exc:
        return {"state":"NETWORK_ERROR","error_type":type(exc).__name__,"diagnostic":None}
    if hostname not in ("www.google.com","google.com") or status!=200 or len(body)>MAX_BODY:
        return {"state":"UNEXPECTED_RESPONSE","http_status":status,"diagnostic":None}
    return {"state":"INDEX_RECEIVED","http_status":status,
            "diagnostic":parse_index_metadata(body.decode("utf-8","replace"))}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="google_index_diagnostic.json")
    args=parser.parse_args()
    result=fetch()
    result.update({"source":"public_google_images_index","target_product":"6221369038",
                   "target_item":"12453073752", "safe_to_publish":False})
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("GOOGLE_IMAGE_INDEX "+json.dumps({
        "state":result["state"],"http_status":result.get("http_status"),
        "cdn_urls_found":(result["diagnostic"] or {}).get("indexed_image_host_count",0),
        "captcha":(result["diagnostic"] or {}).get("captcha_markers_present",False),
        "matched_source_item_photo":False,
    },ensure_ascii=False))

if __name__=="__main__":
    main()
