"""Read-only diagnose posted AFFSDP anchor href split vs the actual exact ID.

Does NOT click links or publish/edit anything, and uses existing owner session.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import parse_daangn_storage_state
from gh_only.daangn_verify_published import POST_URL

OUTPUT=Path("daangn-link-diagnostics");OUTPUT.mkdir(exist_ok=True)

def main():
    data={"attempted_mutation":False,"url":POST_URL,"status":"NOT_STARTED"}
    try:
        session=parse_daangn_storage_state(os.environ["DAANGN_CAFE_STORAGE_STATE_JSON"])
        with sync_playwright() as play:
            browser=play.chromium.launch(headless=True)
            try:
                page=browser.new_context(storage_state=session,locale="ko-KR").new_page()
                response=page.goto(POST_URL,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(4500)
                data["http_status"]=response.status if response else None
                data["hrefs"]=page.evaluate("""() => {
                   return [...document.querySelectorAll('a')].filter(
                       a=>a.href.includes('coupang.com') || a.textContent.includes('AFFSDP')
                   ).map(a=>({
                       text:a.innerText?.slice(0,280),
                       href:a.getAttribute('href')?.slice(0,600),
                       outerHTML:a.outerHTML.slice(0,1300),
                       parentHTML:a.parentElement?.outerHTML.slice(0,1800)
                   })).slice(0,15)
                }""")
                for a in data["hrefs"]:
                    link=a.get("href") or ""
                    try:
                        p=urlsplit(link)
                        qs=parse_qs(p.query)
                        a["href_itemId"]=qs.get("itemId",[None])[0]
                        a["href_pageKey"]=qs.get("pageKey",[None])[0]
                    except ValueError:
                        pass
                body=page.locator("body").inner_text(timeout=5000)
                m=re.search(r"itemId=12814.{0,30}",body,re.S)
                data["text_around_itemId"]=m.group(0) if m else None
                data["status"]="READ_SUCCESS"
            finally:
                browser.close()
    except Exception as exc:
        data["status"]="READ_FAILED"
        data["error_type"]=type(exc).__name__
    (OUTPUT/"diagnosis.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_LINK_DIAGNOSIS "+json.dumps(data,ensure_ascii=False))
    if data["status"]!="READ_SUCCESS":
        raise SystemExit(2)

if __name__=="__main__":main()
