"""One-shot metadata-only HEAD of an explicitly unissued AFFSDP candidate.

Tests whether link.coupang.com adds tracking fields to redirected product
URL. Never claims commission eligibility; no login, purchase or GET request.
"""
import json
from urllib.parse import urlsplit,parse_qs
from urllib.error import HTTPError,URLError
from urllib.request import build_opener,HTTPRedirectHandler,Request

URL=("https://link.coupang.com/re/AFFSDP?"
     "lptag=AF9668915&pageKey=7164345502&itemId=17686805619&vendorItemId=80519514876")
OWNER_TAG="AF9668915"
class Stop(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None

def main():
    result={"candidate_is_officially_issued":False,"commission_attribution_proven":False,
            "method":"HEAD"}
    try:
        response=build_opener(Stop).open(Request(URL,method="HEAD"),timeout=15)
    except HTTPError as e:
        response=e
    except (URLError,OSError) as e:
        result["error_type"]=type(e).__name__
        print(json.dumps(result,indent=2))
        return
    with response:
        result["status"]=response.code
        raw=response.headers.get("Location","")
    if not raw:
        result["redirect_present"]=False
        print(json.dumps(result,indent=2))
        return
    parsed=urlsplit(raw)
    if parsed.scheme!="https" or parsed.hostname not in {"www.coupang.com","coupang.com","link.coupang.com"}:
        result["result"]="unexpected_host"
    else:
        params=parse_qs(parsed.query,keep_blank_values=True)
        result.update({"redirect_present":True,"destination_host":parsed.hostname,
                       "destination_path":parsed.path,
                       "query_keys":sorted(params),
                       "lptag_matches_owner":params.get("lptag")==[OWNER_TAG],
                       "sig_key_present":"sig" in params,
                       "sig_nonempty":any(params.get("sig",[])),
                       "token_key_present":"token" in params,
                       "token_nonempty":any(params.get("token",[])),
                       "traceid_present":"traceid" in params,
                       "clickbeacon_present":"clickBeacon" in params,
                       "campaignid_present":"campaignid" in params,
                       "subid_present":"subid" in params})
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
