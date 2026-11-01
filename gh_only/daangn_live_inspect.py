"""One-time authenticated Daangn Cafe editor structure inspection.

Inspects only the explicitly supplied user's cafe /posts/new. Does not fill
content, click submit or publish. Do not log cookies, session state, or DOM HTML.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from gh_only.daangn_editor_probe import parse_daangn_storage_state
from playwright.sync_api import sync_playwright

TARGET = "https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
OUTPUT = Path("daangn-post-test")
OUTPUT.mkdir(exist_ok=True)

def brief_text(s):
    if not isinstance(s, str):
        return ""
    return " ".join(s.split())[:85]

def view_structure(page):
    # No input content, values, cookie/token or page text is logged.
    return page.evaluate("""() => {
        const clean = s => (s||'').toString().trim().replace(/\\s+/g,' ').slice(0,85);
        const visible = el => {
          const r=el.getBoundingClientRect(); const st=getComputedStyle(el);
          return r.width>0 && r.height>0 && st.visibility!=='hidden' && st.display!=='none';
        };
        const nodes = (sel,limit=35) => [...document.querySelectorAll(sel)].filter(visible).slice(0,limit);
        return {
          labels: nodes('label').map(e=>clean(e.textContent)),
          buttons: nodes('button, [role=button]',60).map(e=>({
            text:clean(e.innerText||e.textContent),aria:clean(e.getAttribute('aria-label')),type:e.getAttribute('type'),
            disabled:!!e.disabled,
          })),
          text_inputs: nodes('input:not([type=hidden]), textarea',30).map(e=>({
            type:e.type,placeholder:clean(e.getAttribute('placeholder')),name:clean(e.getAttribute('name')),
            aria:clean(e.getAttribute('aria-label')),accept:clean(e.getAttribute('accept')),
            outerTag:e.tagName,
          })),
          editors: nodes('[contenteditable=true], [role=textbox]',20).map(e=>({
            tag:e.tagName, role:e.getAttribute('role'),
            dataPlaceholder:clean(e.getAttribute('data-placeholder')),
            placeholder:clean(e.getAttribute('placeholder')),
            className:clean(typeof e.className==='string'?e.className:''),
          })),
          select_options: nodes('select',10).map(e=>({
            id:e.id, options:[...e.options].map(x=>clean(x.textContent)).slice(0,30)
          })),
          navlinks: nodes('a',30).map(e=>({text:clean(e.innerText||e.textContent),hrefPath:(()=>{try{return new URL(e.href).pathname}catch{return ''}})()})),
          headings:nodes('h1,h2,h3',18).map(e=>clean(e.innerText)),
          topText: clean(document.body.innerText).slice(0,85)
        };
    }""")

def main():
    result = {
        "target": TARGET,
        "login_session_available": False,
        "post_published": False,
        "submit_clicked": False,
        "status": "NOT_STARTED",
    }
    secret = os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON", "")
    if not secret:
        result["status"]="NO_GITHUB_SECRET_DAANGN_CAFE_STORAGE_STATE_JSON"
    else:
        try:
            state = parse_daangn_storage_state(secret)
            result["login_session_available"]=True
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    ctx=browser.new_context(storage_state=state, locale="ko-KR",
                                           viewport={"width":1366,"height":950})
                    page=ctx.new_page()
                    response=page.goto(TARGET,wait_until="domcontentloaded",timeout=30000)
                    page.wait_for_timeout(6500)
                    result["http_status"]=response.status if response else None
                    u=urlsplit(page.url)
                    result["current_host"]=u.hostname
                    result["current_path"]=u.path
                    result["title"]=brief_text(page.title())
                    result["structure"]=view_structure(page)
                    result["status"]="INSPECTED"
                    page.screenshot(path=str(OUTPUT/"editor_auth_screen.png"),
                                    full_page=False,timeout=10000)
                finally:
                    browser.close()
        except Exception as e:
            result["status"]="ERROR"
            result["error_type"]=type(e).__name__
    (OUTPUT/"site_structure.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_EDITOR_LIVE_DIAGNOSTIC "+json.dumps(result,ensure_ascii=False))
    if result["status"] in {"NO_GITHUB_SECRET_DAANGN_CAFE_STORAGE_STATE_JSON","ERROR"}:
        raise SystemExit(2)

if __name__=="__main__":
    main()
