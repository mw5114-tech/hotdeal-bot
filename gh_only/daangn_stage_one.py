"""Stage exactly ONE Jirum-derived post in the user's Daangn Cafe editor.

No submit here. Verifies destination, category, title, body, photo input,
and exposes only interface control metadata for a carefully gated one-time
publish followup. Does not print cookies or full post body.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import read_one_draft, parse_daangn_storage_state, DISCLOSURE

TARGET="https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
ROOT=Path("daangn-post-test");ROOT.mkdir(exist_ok=True)
SOURCE=Path("jirum-affsdp-drafts")

def get_payload():
    item=read_one_draft(SOURCE)
    text=item["body"]
    original_line="🛒 상품 링크 (수익 귀속 미검증) : "
    if not text.splitlines()[-2].startswith(original_line):  # existing code appends disclosure last
        raise ValueError("UNEXPECTED_LINK_DISCLOSURE_LAYOUT")
    lines=text.splitlines()
    # The last line must be the owner's URL as expressly requested. Disclosure precedes it.
    ownlink=lines[-2]
    body="\n".join(lines[:-2]).rstrip()+"\n\n"+DISCLOSURE+"\n\n"+ownlink
    if not body.endswith(ownlink) or not item["image"]:
        raise ValueError("NO_EXACT_PRODUCT_IMAGE_OR_OWN_URL")
    return {**item,"body":body}

def main():
    result={"status":"NOT_STARTED","source_message_id":None,"post_published":False,
            "submit_clicked":False,"direct_editor":TARGET}
    try:
        payload=get_payload()
        result["source_message_id"]=payload["source_id"]
        session=parse_daangn_storage_state(os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON",""))
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                ctx=browser.new_context(storage_state=session,locale="ko-KR",
                                        viewport={"width":1366,"height":950})
                page=ctx.new_page()
                response=page.goto(TARGET,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(2500)
                if not response or response.status!=200 or urlsplit(page.url).path!="/i-gagyeog-mos-c/posts/new":
                    raise RuntimeError("WRONG_SITE_OR_LOGIN_REQUIRED")
                board=page.locator("select")
                if board.count()!=1:
                    raise RuntimeError("BOARD_SELECTOR_AMBIGUOUS")
                board.select_option(label="🔥 오늘의 핫딜")
                result["board_name"]=board.locator("option:checked").inner_text()
                if result["board_name"]!="🔥 오늘의 핫딜":
                    raise RuntimeError("BOARD_NOT_SELECTED")
                title=page.locator('input[placeholder="제목을 입력해주세요."]')
                if title.count()!=1:
                    raise RuntimeError("TITLE_SELECTOR_AMBIGUOUS")
                title.fill(payload["title"])
                result["title_filled"]=title.input_value()==payload["title"]
                editor=page.locator("div.ProseMirror[contenteditable=true]")
                if editor.count()!=1:
                    raise RuntimeError("BODY_SELECTOR_AMBIGUOUS")
                editor.fill(payload["body"])
                result["body_filled"]=editor.inner_text().strip()==payload["body"].strip()
                result["link_is_last_line"]=editor.inner_text().strip().splitlines()[-1].startswith("🛒 상품 링크 (수익 귀속 미검증)")
                result["disclosure_present"]=DISCLOSURE in editor.inner_text()

                # Use native file chooser from the site's existing image-add button.
                imgbutton=page.get_by_role("button",name="이미지 추가")
                if imgbutton.count()!=1:
                    raise RuntimeError("IMAGE_ADD_BUTTON_AMBIGUOUS")
                with page.expect_file_chooser(timeout=10000) as choose:
                    imgbutton.click(timeout=6000)
                chooser=choose.value
                chooser.set_files(str(payload["image"]))
                page.wait_for_timeout(4500)
                result["file_input_count"]=page.locator('input[type=file]').count()
                result["image_elements_in_editor"]=editor.locator("img").count()
                result["image_attached_in_dom"]=result["image_elements_in_editor"]>=1
                result["submit_candidates"]=page.evaluate("""() => [...document.querySelectorAll('button,[role=button]')].filter(el=>/글쓰기|등록|게시/.test((el.innerText||el.textContent||'').trim()) && el.getBoundingClientRect().width>0).map(el=>({
                  tag:el.tagName, text:(el.innerText||el.textContent||'').trim().slice(0,35),
                  type:el.getAttribute('type'),disabled:el.disabled===true,aria:el.getAttribute('aria-label'),
                  className:(typeof el.className==='string'?el.className:'').slice(0,120),
                  outerHTML:el.outerHTML.slice(0,420), x:Math.round(el.getBoundingClientRect().x),
                  y:Math.round(el.getBoundingClientRect().y)
                })).slice(0,15)""")
                result["status"]="EDITOR_STAGED_WITH_PHOTO" if (
                    result["title_filled"] and result["body_filled"] and
                    result["link_is_last_line"] and result["disclosure_present"] and
                    result["image_attached_in_dom"]) else "EDITOR_STAGING_INCOMPLETE"
                page.screenshot(path=str(ROOT/"staged_editor.png"),full_page=False,timeout=10000)
            finally:
                browser.close()
    except Exception as exc:
        result["status"]="ERROR"
        result["error_type"]=type(exc).__name__
        result["error_message"]=str(exc)[:160] if not isinstance(exc,(ValueError,RuntimeError)) else str(exc)
    (ROOT/"stage_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_STAGE_RESULT "+json.dumps(result,ensure_ascii=False))
    if result["status"]!="EDITOR_STAGED_WITH_PHOTO":
        raise SystemExit(2)

if __name__=="__main__":
    main()
