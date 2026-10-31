"""One-shot, non-authenticated GitHub runner OS comparison for Coupang Partners.

Per job: exactly one normal HTTPS GET; no cookies, credentials, login, product
pages, retries, stealth, proxies, or browser fingerprint modifications.
One HTTP 403 means only this runner/network path was denied, not that every
GitHub execution environment is impossible.
"""
from __future__ import annotations
from urllib import request, error
from urllib.parse import urlsplit
import json
import os
import platform

URL="https://partners.coupang.com/"
def check_once():
    req=request.Request(URL,headers={"Accept":"text/html"})
    try:
        with request.urlopen(req,timeout=20) as resp:
            final=urlsplit(resp.geturl())
            return {"status":resp.status,"result":"HTTP_RESPONSE",
                    "final_host":final.hostname,"content_type":resp.headers.get_content_type()}
    except error.HTTPError as exc:
        return {"status":exc.code,"result":"HTTP_DENIED_OR_ERROR"}
    except Exception as exc:
        return {"status":None,"result":"NETWORK_ERROR","error_type":type(exc).__name__}
if __name__=="__main__":
    outcome={"os":platform.system(),"runner_os":os.environ.get("RUNNER_OS"),
             "target_host":"partners.coupang.com","signed_in":False,
             "attempts":1,"result":check_once(),"affiliate_link_created":False}
    print("PARTNERS_GITHUB_RUNNER_COMPARE "+json.dumps(outcome,ensure_ascii=False))
