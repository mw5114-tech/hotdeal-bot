"""Publish the ONE owner-requested second Daangn test, image before text.

Fail-closed: exact source 94738, category Hotdeal, contiguous long AFFSDP
URL in editor, loaded photo, one click only, then read-only verify anchor href
and rendered post image. NEVER retry after a click attempt.
"""
from __future__ import annotations
import json,os,re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import parse_daangn_storage_state
from gh_only.daangn_image_first_stage import get_article,open_prepare,BOARD,TARGET,OUT

SOURCE_ID=94738
POST_PREFIX="/i-gagyeog-mos-c/posts/"
REQUIRED=("board_correct","image_attached_first","image_loaded_first",
          "title_filled_after_image","body_typed_after_image",
          "owner_url_last","image_survived_text","disclosure_present",
          "url_intact_in_one_text_node")

def run_once():
    result={"status":"NOT_STARTED","source_id":None,"submit_click_attempted":False,
            "post_url":None,"confirmed_posted":False,"full_url_clickable":False,
            "full_item_id_in_href":False,"published_photo_loaded":False}
    if not (os.getenv("GITHUB_ACTIONS")=="true"
            and os.getenv("GITHUB_RUN_ATTEMPT")=="1"
            and os.getenv("GITHUB_EVENT_NAME")=="push"
            and os.getenv("GITHUB_REF_NAME")=="test/daangn-image-first-20261101"
            and os.getenv("ALLOW_ONE_NEW_IMAGE_FIRST_POST")=="true"):
        return {**result,"status":"NOT_AUTHORIZED_RUN"}
    try:
        article=get_article()
        result["source_id"]=article["source_id"]
        if int(article["source_id"])!=SOURCE_ID:
            return {**result,"status":"SOURCE_ID_CHANGED_ABORT"}
        state=parse_daangn_storage_state(os.environ["DAANGN_CAFE_STORAGE_STATE_JSON"])
        with sync_playwright() as play:
            browser=play.chromium.launch(headless=True)
            try:
                ctx=browser.new_context(storage_state=state,locale="ko-KR",
                                        viewport={"width":1366,"height":950})
                page=ctx.new_page()
                checks=open_prepare(page,article)
                result["prepublish_checks"]=checks
                if not all(checks.get(key) is True for key in REQUIRED):
                    return {**result,"status":"PREPUBLISH_VALIDATION_FAILED"}
                # Ensure one text node really holds the complete itemId;
                # no image can have been inserted inside the last URL.
                result["full_url_verified_before_click"]=checks["url_intact_in_one_text_node"]
                submit=page.locator("button.w-full").filter(has_text=re.compile(r"^글쓰기$"))
                if submit.count()!=1 or not submit.is_enabled():
                    raise RuntimeError("SUBMIT_BUTTON_NOT_UNIQUE")
                page.screenshot(path=str(OUT/"second_post_before_submit.png"),timeout=10000)
                # One irreversible attempt. Never retry this source on failure.
                result["submit_click_attempted"]=True
                submit.click(timeout=10000)
                result["submit_clicked_once"]=True
                page.wait_for_timeout(7500)
                u=urlsplit(page.url)
                result["after_click_path"]=u.path
                if (u.scheme!="https" or u.hostname!="cafe.daangn.com"
                    or not u.path.startswith(POST_PREFIX)
                    or u.path==POST_PREFIX+"new"):
                    result["status"]="PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                    return result
                result["post_url"]=page.url
                text=page.locator("body").inner_text(timeout=4000)
                result["title_visible"]=article["title"] in text
                result["category_visible"]="오늘의 핫딜" in text
                result["confirmed_posted"]=result["title_visible"] and result["category_visible"]
                # Check the ACTUAL published anchor, not only raw body text.
                anchors=page.locator('a[href*="/re/AFFSDP"]').evaluate_all(
                    "(nodes)=>nodes.map(a=>a.getAttribute('href'))")
                result["actual_affiliate_anchors"]=len(anchors)
                result["full_url_clickable"]=(len(anchors)==1
                                               and anchors[0]==article["candidate_url"])
                result["full_item_id_in_href"]=(
                    result["full_url_clickable"]
                    and parse_qs(urlsplit(anchors[0]).query).get("itemId")==[article["item_id"]])
                result["published_photo_loaded"]=page.locator('img[alt^="Post image"]').evaluate_all(
                    "(nodes)=>nodes.some(i=>i.complete&&i.naturalWidth>0&&i.naturalHeight>0)")
                result["post_body_contains_disclosure"]="쿠팡 파트너스 활동의 일환" in text
                result["status"]=(
                    "PUBLISHED_AND_FULL_LINK_VERIFIED"
                    if all([result["confirmed_posted"],result["full_url_clickable"],
                            result["full_item_id_in_href"],result["published_photo_loaded"],
                            result["post_body_contains_disclosure"]])
                    else "POST_EXISTS_BUT_CONTENT_VERIFICATION_FAILED_DO_NOT_RETRY")
                page.screenshot(path=str(OUT/"second_post_after_submit.png"),timeout=10000)
            finally:
                browser.close()
    except Exception as exc:
        result["status"]=("PUBLISH_UNCERTAIN_DO_NOT_RETRY" if result["submit_click_attempted"]
                          else "ABORTED_BEFORE_SUBMIT")
        result["error_type"]=type(exc).__name__
        result["error_reason"]=(str(exc)[:130] if isinstance(exc,(RuntimeError,ValueError))
                                else "BROWSER_ACTION_FAILED")
    return result

if __name__=="__main__":
    value=run_once()
    OUT.mkdir(exist_ok=True)
    (OUT/"publish_result.json").write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_IMAGE_FIRST_PUBLISH "+json.dumps(value,ensure_ascii=False))
    if value["status"]!="PUBLISHED_AND_FULL_LINK_VERIFIED":
        raise SystemExit(2)
