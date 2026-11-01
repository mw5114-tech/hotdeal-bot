"""Resolve one supplied Coupang source short URL into exact product identity.

At most one HEAD and one GET sequence, no login, cookies or purchases.
Redirection is restricted to first-party Coupang HTTPS hosts and product URL.
The request to an existing affiliate link may count as a referral click.
"""
from __future__ import annotations
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.error import HTTPError, URLError
from coupang_fresh_link_research import extract_product_identity, product_url

SOURCE="https://link.coupang.com/a/ihyVQ9K1qC"
HOSTS={"link.coupang.com","www.coupang.com","coupang.com","m.coupang.com"}
MAX_REDIRECTS=5

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

def walk(method):
    opener=build_opener(NoRedirect)
    current=SOURCE
    chain=[]
    for step in range(MAX_REDIRECTS+1):
        parsed=urlsplit(current)
        if parsed.scheme!="https" or parsed.hostname not in HOSTS or parsed.username or parsed.password:
            return {"result":"NON_COUPANG_REDIRECT","chain":chain}
        try:
            identity=extract_product_identity(current)
            return {"result":"RESOLVED_EXACT_ITEM","identity":identity,"direct_product":product_url(identity),"chain":chain}
        except ValueError:
            pass
        request=Request(current,method=method,headers={"User-Agent":"Mozilla/5.0 (compatible; OneLinkDiagnostics/1.0)","Accept":"text/html"})
        try:
            res=opener.open(request,timeout=18)
        except HTTPError as e:
            res=e
        except (URLError,OSError) as e:
            return {"result":"NETWORK_FAILURE","error_type":type(e).__name__,"chain":chain}
        with res:
            status=res.status if hasattr(res,"status") else res.code
            location=res.headers.get("Location")
            chain.append({"host":parsed.hostname,"status":status,"redirected":bool(location)})
            if not location:
                return {"result":"NO_HTTP_LOCATION","chain":chain}
            new_url=urljoin(current,location)
            parsed_new=urlsplit(new_url)
            if parsed_new.scheme!="https" or parsed_new.hostname not in HOSTS:
                return {"result":"UNEXPECTED_REDIRECT_TARGET","chain":chain}
            current=new_url
    return {"result":"REDIRECT_LIMIT","chain":chain}

if __name__=="__main__":
    x=walk("HEAD")
    if x.get("result")!="RESOLVED_EXACT_ITEM":
        x={"head":x,"get":walk("GET")}
    print(json.dumps(x,ensure_ascii=False,indent=2))
