"""One explicitly authorized single Daangn Cafe test post, fail-closed.

Runs only from specially created one-shot workflow on first GitHub Actions
attempt, in user's own precisely identified cafe/board.
Checks item link and photo, clicks Publish exactly once, never retries.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import parse_daangn_storage_state, DISCLOSURE
from gh_only.daangn_stage_one import get_payload, TARGET, ROOT

EXPECTED_SLUG="/i-gagyeog-mos-c"
BOARD="🔥 오늘의 핫딜"
OUTPUT=ROOT

def norm(s):
    return re.sub(r"\s+","",s or "")

def publish_once():
    result={"status":"NOT_STARTED","post_published_verified":False,
            "submit_click_attempted":False,"clicked_once":False,
            "category":None,"post_url":None,"source_id":None}
    # Must never be run manually or twice.
    if os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_RUN_ATTEMPT") != "1" or os.getenv("ALLOW_ONE_TEST_POST") != "true":
        return {**result,"status":"NOT_AUTHORIZED_ONE_TIME_GITHUB_RUN"}
    try:
        article=get_payload()
        result["source_id"]=article["source_id"]
        # Prefer the inspected exact source item; do not silently post new,
        # uninspected Jirum items in this one-time operation.
        if int(article["source_id"])!=94737:
            return {**result,"status":"SOURCE_POST_ID_CHANGED"}
        state=parse_daangn_storage_state(os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON",""))
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                ctx=browser.new_context(storage_state=state,locale="ko-KR",
                                       viewport={"width":1366,"height":950})
                page=ctx.new_page()
                resp=page.goto(TARGET,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(2200)
                if not resp or resp.status!=200 or urlsplit(page.url).path != EXPECTED_SLUG+"/posts/new":
                    raise RuntimeError("WRONG_CAFE_OR_LOGIN_REQUIRED")
                board=page.locator("select")
                if board.count()!=1:
                    raise RuntimeError("BOARD_SELECT_MISSING_OR_AMBIGUOUS")
                board.select_option(label=BOARD)
                if board.locator("option:checked").inner_text()!=BOARD:
                    raise RuntimeError("WRONG_BOARD_SELECTED")
                result["category"]=BOARD
                title=page.locator('input[placeholder="제목을 입력해주세요."]')
                if title.count()!=1: raise RuntimeError("TITLE_INPUT_MISSING")
                title.fill(article["title"])
                if title.input_value()!=article["title"]:raise RuntimeError("TITLE_NOT_VERIFIED")

                editor=page.locator("div.ProseMirror[contenteditable=true]")
                if editor.count()!=1:raise RuntimeError("RICH_EDITOR_NOT_UNIQUE")
                editor.fill(article["body"])
                actual=editor.inner_text().strip()
                if norm(actual)!=norm(article["body"]):
                    raise RuntimeError("BODY_MISMATCH")
                if actual.splitlines()[-1] != article["body"].splitlines()[-1]:
                    raise RuntimeError("OWNER_LINK_NOT_LAST")
                if DISCLOSURE not in actual or "AF9668915" not in actual:
                    raise RuntimeError("DISCLOSURE_OR_OWNER_LPTAG_MISSING")
                if "https://link.coupang.com/a/" in actual:
                    raise RuntimeError("SOURCE_AFFILIATE_LINK_LEAK")
                result["last_line_is_owner_link"]=True

                upload=page.get_by_role("button",name="이미지 추가")
                if upload.count()!=1:raise RuntimeError("IMAGE_ADD_NOT_UNIQUE")
                with page.expect_file_chooser(timeout=11000) as picker:
                    upload.click(timeout=6500)
                picker.value.set_files(str(article["image"]))
                page.wait_for_timeout(4700)
                if editor.locator("img").count()!=1:
                    raise RuntimeError("PHOTO_NOT_CONFIRMED_IN_EDITOR")
                result["photo_embedded"]=True

                # Confirm after upload, since the editor may re-render.
                if board.locator("option:checked").inner_text()!=BOARD:
                    raise RuntimeError("CATEGORY_RESET_AFTER_IMAGE")
                if norm(editor.inner_text().strip())!=norm(article["body"]):
                    raise RuntimeError("BODY_CHANGED_AFTER_IMAGE")

                # The actual observed submit is the full-width second '글쓰기'
                # button in the article form. The header button is separate.
                submit=page.locator("button.w-full").filter(has_text=re.compile(r"^글쓰기$"))
                if submit.count()!=1 or not submit.is_enabled():
                    raise RuntimeError("SUBMIT_BUTTON_NOT_UNIQUELY_IDENTIFIED")
                page.screenshot(path=str(OUTPUT/"before_submit.png"),timeout=10000)
                # Exactly one intentional publish click. No retry under ANY outcome.
                result["submit_click_attempted"]=True
                submit.click(timeout=8000)
                result["clicked_once"]=True
                page.wait_for_timeout(8500)
                after=urlsplit(page.url)
                result["after_click_host"]=after.hostname
                result["after_click_path"]=after.path
                body_now=page.locator("body").inner_text(timeout=3500)
                result["title_visible_after_click"]=article["title"] in body_now
                if (after.hostname=="cafe.daangn.com"
                    and re.fullmatch(r"/i-gagyeog-mos-c/posts/[A-Za-z0-9_-]{2,128}",after.path)
                    and after.path!=EXPECTED_SLUG+"/posts/new"
                    and result["title_visible_after_click"]):
                    result["post_published_verified"]=True
                    result["post_url"]=page.url
                    result["status"]="PUBLISHED_VERIFIED"
                else:
                    result["status"]="PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                page.screenshot(path=str(OUTPUT/"after_submit.png"),timeout=10000)
            finally:
                browser.close()
    except Exception as exc:
        # If click was attempted, publishing could have succeeded despite a
        # navigation/error. NEVER blindly retry.
        result["status"]="PUBLISH_UNCERTAIN_DO_NOT_RETRY" if result["submit_click_attempted"] else "ABORTED_BEFORE_PUBLISH"
        result["error_type"]=type(exc).__name__
        result["error_reason"]=str(exc)[:140] if isinstance(exc,(RuntimeError,ValueError)) else "BROWSER_ACTION_FAILED"
    return result

if __name__=="__main__":
    r=publish_once()
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT/"publish_result.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_ONE_POST_RESULT "+json.dumps(r,ensure_ascii=False))
    if r["status"]!="PUBLISHED_VERIFIED":
        raise SystemExit(2)
