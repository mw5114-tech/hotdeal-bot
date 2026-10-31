"""One-shot authenticated-page reachability diagnostic WITHOUT credentials.

Compare a standard Python GET with a normal Chromium browser visit to the
publicly documented legacy quick-link hash route. No stealth/captcha/proxy,
no retries, no login attempt, no link creation. Both use the same GitHub runner.
"""
from __future__ import annotations

from pathlib import Path
from urllib import request, error
from urllib.parse import urlsplit
import json

ENTRY = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
HTTP_URL = "https://partners.coupang.com/"  # hash fragments do not go to server


def python_http_once():
    try:
        req = request.Request(HTTP_URL, headers={"Accept":"text/html"})
        with request.urlopen(req, timeout=18) as response:
            return {"status": response.status, "result":"HTTP_OK",
                    "content_type": response.headers.get_content_type()}
    except error.HTTPError as exc:
        return {"status": exc.code, "result":"HTTP_DENIED"}
    except Exception as exc:
        return {"status": None, "result":"HTTP_NETWORK_ERROR",
                "error_class":type(exc).__name__}


def chromium_hash_route_once():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    with sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            ctx=browser.new_context(locale="ko-KR")
            page=ctx.new_page()
            response=page.goto(ENTRY, wait_until="domcontentloaded",timeout=27000)
            try:
                page.wait_for_load_state("networkidle",timeout=8500)
            except PWTimeout:
                pass
            u=urlsplit(page.url)
            body=" ".join(page.locator("body").inner_text(timeout=8000).split())
            result = {
                "result":"BROWSER_LOADED",
                "status": response.status if response else None,
                "host":u.hostname,"path":u.path,
                "hash_route_visible":u.fragment,
                "page_title":page.title()[:100],
                "access_denied": "access denied" in body.casefold(),
                "login_label": "로그인" in body,
                "quick_link_label": "간편 링크" in body or "간편링크" in body,
                "coupang_url_input_count":page.locator('input[placeholder*="쿠팡"]').count(),
                "generated_partner_link":False
            }
            ctx.close()
            return result
        finally:
            browser.close()


def main():
    output={"schema":"partners-two-ordinary-github-clients-v1",
            "tested_url":ENTRY,
            "anonymous_http":python_http_once(),
            "browser_hash":{},
            "login_attempted":False, "links_created":0,
            "anti_bot_bypass_attempted":False}
    try:
        output["browser_hash"]=chromium_hash_route_once()
    except Exception as exc:
        output["browser_hash"]={"result":"BROWSER_ERROR",
                                "error_class":type(exc).__name__}
    Path("partners_access_methods.json").write_text(
        json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("COUPANG_PARTNERS_ACCESS_METHODS "+json.dumps(output,ensure_ascii=False))


if __name__=="__main__":
    main()
