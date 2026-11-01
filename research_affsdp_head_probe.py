"""One-time, unauthenticated HEAD check of a hypothetical AFFSDP link.

This is NOT an officially issued affiliate link; HTTP redirects do not prove
tracking attribution, eligibility or commission. No GET, clicks, login or sale.
"""
from __future__ import annotations
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
from coupang_fresh_link_research import extract_product_identity, hypothetical_landing_url

IDENTITY={"productId":"7164345502","itemId":"17686805619","vendorItemId":"80519514876"}
TAG="AF9668915"

class DisableRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def main():
    url=hypothetical_landing_url(IDENTITY,TAG)
    r={"experiment_link_created":True,"official_link_issued":False,
       "attribution_verified":False,"head_request_made":True}
    try:
        response=build_opener(DisableRedirect).open(Request(url,method="HEAD"),timeout=18)
    except HTTPError as exc:
        response=exc
    except (URLError,OSError) as exc:
        r.update({"network_error_type":type(exc).__name__})
        print(json.dumps(r,indent=2))
        return 0
    with response:
        status=response.code
        destination=response.headers.get("Location","")
        host=urlsplit(destination).hostname if destination else None
        r.update({"http_status":status,"redirect_host":host,"redirect_present":bool(destination)})
        if destination:
            try:
                identity=extract_product_identity(destination)
                r["redirect_exact_same_product_and_option"]=identity==IDENTITY
            except ValueError:
                r["redirect_exact_same_product_and_option"]=False
    print(json.dumps(r,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
