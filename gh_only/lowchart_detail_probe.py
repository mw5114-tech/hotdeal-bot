"""One-shot detail check of exact product/item route on Lowchart (public page).

The route is a hypothesis based on documented Lowchart URL patterns;
being reachable alone cannot prove that the service observed the exact item.
This tool reports brief visible-price-history snippets, no hidden APIs or posts.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib import request, error
from urllib.parse import urlsplit
from gh_only.price_history_probe import HTMLInfo

URL = "https://www.lowchart.com/6221369038-12453073752"
LABELS = ("평균", "최저", "최고", "추이", "최근", "가격 기록", "현재가", "가격 이력", "쿠팡상품번호", "원")
MAX_READ = 1_800_000

def read():
    req = request.Request(URL, headers={"Accept":"text/html", "User-Agent":"GitHubHotdealHistoryOneShotDiagnostic/1.0"})
    try:
        with request.urlopen(req, timeout=22) as r:
            final_host=urlsplit(r.geturl()).hostname
            status=r.status
            body=r.read(MAX_READ + 1)
    except error.HTTPError as err:
        return {"state":"HTTP_ERROR","http_status":err.code}
    except Exception as err:
        return {"state":"NETWORK_ERROR","error_class":type(err).__name__}
    if status != 200 or final_host not in ("www.lowchart.com", "lowchart.com") or len(body) > MAX_READ:
        return {"state":"BAD_RESPONSE","http_status":status}
    html=body.decode("utf-8","replace")
    parser=HTMLInfo()
    parser.feed(html)
    text=" ".join(" ".join(parser.text_chunks).split())
    spans=[]
    for term in LABELS[:-1]:
        matches=list(re.finditer(re.escape(term),text))
        for m in matches[:4]:
            segment=text[max(0,m.start()-65):min(len(text),m.end()+130)]
            if segment not in spans:
                spans.append(segment)
    return {
        "state":"PAGE_PARSED",
        "http_status":status,
        "title":parser.title[:180],
        "og_title":parser.metas.get("og:title"),
        "contains_target_product_id":"6221369038" in html,
        "contains_target_item_id":"12453073752" in html,
        "matching_exact_coupang_item_link": any("itemId=12453073752" in href and "/vp/products/6221369038" in href for href in parser.product_links),
        "visible_excerpt_first_380":text[:380],
        "history_label_snippets":spans[:15],
        "content_bytes":len(body),
        "price_history_exact_item_confirmed":False
    }

if __name__=="__main__":
    r=read()
    Path("lowchart_detail_diagnostic.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("LOWCHART_DETAIL "+json.dumps(r,ensure_ascii=False))
