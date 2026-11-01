"""Stage ONE distinct Jirum deal in user's Daangn editor: IMAGE FIRST, TEXT SECOND.

Fail closed unless uploaded image survives, full AFFSDP URL is one intact
clickable anchor with exact itemId, correct board, correct title, and body.
No submission in this stage. Real publish requires a separate one-shot runner.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import read_one_draft,parse_daangn_storage_state,DISCLOSURE

TARGET="https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
BOARD="🔥 오늘의 핫딜"
OUT=Path("daangn-image-first-check")
OUT.mkdir(exist_ok=True)

def compact(s):
    return re.sub(r"\s+","",s or "")

def get_article(folder=Path("jirum-affsdp-drafts")):
    article=read_one_draft(folder)
    if str(article["source_id"])=="94737":
        raise ValueError("PREVIOUSLY_PUBLISHED_SOURCE_ID_BLOCKED")
    suffix="\n\n"+DISCLOSURE
    if not article["body"].endswith(suffix):
        raise ValueError("DISCLOSURE_SUFFIX_NOT_FOUND")
    caption=article["body"][:-len(suffix)]
    lines=caption.splitlines()
    link_line=lines[-1]
    prefix="🛒 상품 링크 (수익 귀속 미검증) : "
    if not link_line.startswith(prefix):
        raise ValueError("EXPECTED_OWNER_LINK_MISSING")
    url=link_line[len(prefix):]
    u=urlsplit(url)
    q=parse_qs(u.query)
    if (u.scheme!="https" or u.hostname!="link.coupang.com"
        or u.path!="/re/AFFSDP"
        or q.get("lptag")!=["AF9668915"]
        or len(q.get("pageKey",[]))!=1 or len(q.get("itemId",[]))!=1
        or not q["itemId"][0].isdigit()):
        raise ValueError("OWNER_AFFSDP_IDENTITY_INVALID")
    original=folder/"drafts.json"
    data=json.loads(original.read_text("utf-8"))["drafts"][0]
    identity=data.get("product_identity") or {}
    if (q["pageKey"][0]!=identity.get("product_id")
        or q["itemId"][0]!=identity.get("item_id")):
        raise ValueError("URL_AND_ORIGINAL_PRODUCT_IDENTITY_MISMATCH")
    # Keep own URL LAST. Explicitly disclose affiliate nature right before link.
    text="\n".join(lines[:-1]).rstrip()+"\n\n"+DISCLOSURE+"\n\n"+link_line
    if not article["image"] or not article["image"].is_file():
        raise ValueError("IMAGE_FILE_MISSING")
    return {**article,"body":text,"candidate_url":url,
            "page_key":q["pageKey"][0],"item_id":q["itemId"][0]}

def open_prepare(page, article):
    result={"board_correct":False,"image_attached_first":False,
            "image_loaded_first":False,"title_filled_after_image":False,
            "body_typed_after_image":False,"anchor_exact":False,
            "owner_url_last":False,"image_survived_text":False,
            "disclosure_present":False}
    resp=page.goto(TARGET,wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(2400)
    if not resp or resp.status!=200 or urlsplit(page.url).path!="/i-gagyeog-mos-c/posts/new":
        raise RuntimeError("WRONG_CAFE_OR_LOGIN_REQUIRED")
    category=page.locator("select")
    if category.count()!=1:raise RuntimeError("BOARD_SELECTOR_AMBIGUOUS")
    category.select_option(label=BOARD)
    result["board_correct"]=category.locator("option:checked").inner_text()==BOARD
    if not result["board_correct"]:raise RuntimeError("WRONG_BOARD")
    editor=page.locator("div.ProseMirror[contenteditable=true]")
    if editor.count()!=1:raise RuntimeError("EDITOR_AMBIGUOUS")

    # FIRST: upload image while rich editor has no text.
    add=page.get_by_role("button",name="이미지 추가")
    if add.count()!=1:raise RuntimeError("IMAGE_ADD_BUTTON_AMBIGUOUS")
    with page.expect_file_chooser(timeout=11000) as picker:
        add.click(timeout=5500)
    picker.value.set_files(str(article["image"]))
    img=editor.locator("img")
    img.first.wait_for(state="visible",timeout=18000)
    page.wait_for_function("""() => {
      const e=document.querySelector('div.ProseMirror[contenteditable=true] img');
      return !!e && e.complete && e.naturalWidth>0;
    }""",timeout=19000)
    result["image_attached_first"]=img.count()==1
    result["image_loaded_first"]=bool(img.first.evaluate("(e)=>e.naturalWidth>0"))
    if not result["image_attached_first"] or not result["image_loaded_first"]:
        raise RuntimeError("IMAGE_NOT_READY_BEFORE_TEXT")

    # SECOND: append text after the image. NEVER use editor.fill(), which
    # clears/replaces rich editor content and can split an active URL.
    editor.click()
    editor.press("ControlOrMeta+End")
    editor.press("Enter")
    for idx,line in enumerate(article["body"].split("\n")):
        if line:
            page.keyboard.insert_text(line)
        if idx < len(article["body"].split("\n"))-1:
            page.keyboard.press("Enter")
    # Press Enter after the complete URL before blur: Tiptap's linkify-on-
    # paste may not activate until the user commits the final paragraph.
    page.keyboard.press("Enter")
    # Move focus out to trigger ProseMirror's autolink recognition.
    title=page.locator('input[placeholder="제목을 입력해주세요."]')
    if title.count()!=1:raise RuntimeError("TITLE_AMBIGUOUS")
    title.fill(article["title"])
    page.wait_for_timeout(1500)
    result["title_filled_after_image"]=title.input_value()==article["title"]
    content=editor.inner_text(timeout=5000).strip()
    result["content_length"]=len(content)
    result["body_typed_after_image"]=(compact(content)==compact(article["body"]))
    result["disclosure_present"]=DISCLOSURE in content
    result["image_survived_text"]=(img.count()==1 and img.first.evaluate("(e)=>e.naturalWidth>0"))

    def href_match():
        values=editor.locator('a[href*="/re/AFFSDP"]').evaluate_all(
            "(es)=>es.map(e=>e.getAttribute('href'))")
        return len(values)==1 and values[0]==article["candidate_url"]
    result["anchor_exact"]=href_match()
    result["anchor_count_initial"]=editor.locator("a").count()
    # Some ProseMirror autolink implementations only run after whitespace.
    if not result["anchor_exact"]:
        editor.press("ControlOrMeta+End")
        page.keyboard.press("Space")
        page.keyboard.press("Backspace")
        title.click()
        page.wait_for_timeout(900)
        result["anchor_exact"]=href_match()
    # Check link is last content text, no orphan digits below uploaded photo.
    content=editor.inner_text(timeout=4500).strip()
    link_line="🛒 상품 링크 (수익 귀속 미검증) : "+article["candidate_url"]
    result["owner_url_last"]=bool(content.splitlines() and content.splitlines()[-1].strip()==link_line)
    # Catch the original bug: the image uploader divided a URL into two
    # separate text nodes/paragraphs, even though whitespace stripping hid it.
    result["url_intact_in_one_text_node"]=editor.evaluate("""(e,expected) => {
      const walk=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
      let n;while((n=walk.nextNode())){ if(n.textContent.includes(expected))return true; }
      return false;
    }""",article["candidate_url"])
    result["body_typed_after_image"]=(compact(content)==compact(article["body"]))
    result["image_survived_text"]=(img.count()==1 and img.first.evaluate("(e)=>e.naturalWidth>0"))
    result["actual_href_item_id"]=parse_qs(urlsplit(
        editor.locator('a[href*="/re/AFFSDP"]').first.get_attribute("href") or "").query).get("itemId",[None])[0] if editor.locator('a[href*="/re/AFFSDP"]').count() else None
    if result["actual_href_item_id"] != article["item_id"]:
        result["anchor_exact"]=False
    return result

def stage_once():
    result={"status":"NOT_STARTED","submitted":False,"source_id":None}
    try:
        article=get_article()
        result["source_id"]=article["source_id"]
        state=parse_daangn_storage_state(os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON",""))
        with sync_playwright() as play:
            browser=play.chromium.launch(headless=True)
            try:
                ctx=browser.new_context(storage_state=state,locale="ko-KR",
                                        viewport={"width":1366,"height":950})
                page=ctx.new_page()
                checks=open_prepare(page,article)
                result["checks"]=checks
                result["status"]="READY_TO_POST" if all([
                    checks["board_correct"],checks["image_attached_first"],
                    checks["image_loaded_first"],checks["title_filled_after_image"],
                    checks["body_typed_after_image"],checks["url_intact_in_one_text_node"],
                    checks["owner_url_last"],checks["image_survived_text"],
                    checks["disclosure_present"]
                ]) else "EDITOR_INCOMPLETE_NO_POST"
                page.screenshot(path=str(OUT/"image_first_stage.png"),
                                full_page=False,timeout=9000)
            finally:
                browser.close()
    except Exception as e:
        result.update({"status":"STAGE_ERROR","error_type":type(e).__name__,
                       "error_reason":str(e)[:125] if isinstance(e,(ValueError,RuntimeError)) else "BROWSER_ERROR"})
    (OUT/"stage_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_IMAGE_FIRST_STAGE "+json.dumps(result,ensure_ascii=False))
    return result

if __name__=="__main__":
    v=stage_once()
    if v["status"]!="READY_TO_POST":
        raise SystemExit(2)
