"""Exactly one user-requested Daangn new-format test post, never retry.

Telegram source 94743 chosen by preceding read-only latest deal preview.
Photo upload precedes body input. Verify full AFFSDP itemId and footer on
published page; do not confuse an unissued URL with commission attribution.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import read_one_draft, parse_daangn_storage_state, DISCLOSURE

TARGET = "https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
PREFIX = "/i-gagyeog-mos-c/posts/"
BOARD = "🔥 오늘의 핫딜"
PINNED_SOURCE = 94743
OUT = Path("daangn-newformat-post")
OUT.mkdir(exist_ok=True)

def norm(s):
    return re.sub(r"\s+", "", s or "")

def article_from_report(folder: Path) -> dict:
    a = read_one_draft(folder)
    if a["source_id"] != PINNED_SOURCE:
        raise ValueError("SOURCE_CHANGED_ABORT_NO_POST")
    if a["source_id"] in (94737,94738):
        raise ValueError("PREVIOUSLY_POSTED_ABORT")
    report = json.loads((folder/"drafts.json").read_text(encoding="utf-8"))
    row = report["drafts"][0]
    cap = row["caption"]
    if (cap.get("sale_price_krw")!=524500
            or cap.get("observed_average_krw")!=692362
            or cap.get("approx_discount_vs_historical_mean_pct")!=25):
        raise ValueError("EXPECTED_LATEST_DEAL_CHANGED_ABORT")
    if (a["image"] is None or not a["image"].is_file()
            or "[25%할인]" not in a["title"]
            or "📦 상품명 :" not in a["body"]
            or "[25%할인]" not in a["body"]
            or "💸 쿠팡 평소 판매가 : 약 692,362원" not in a["body"]
            or "🔥 핫딜 특가 : 524,500원 (약 25% 할인 / 상세페이지 참고)" not in a["body"]
            or "🛒 상품 링크 : "+a["candidate_url"] not in a["body"]
            or "※ 실시간 핫딜 특성상" not in a["body"]
            or "카페 가입 알림 설정하고 꿀템 챙겨가세요." not in a["body"]
            or not a["body"].endswith(DISCLOSURE)
            or a["body"].count(a["candidate_url"])!=1):
        raise ValueError("REQUESTED_POST_FORMAT_NOT_VERIFIED")
    identity=row.get("product_identity") or {}
    query=parse_qs(urlsplit(a["candidate_url"]).query)
    if (query.get("lptag")!=["AF9668915"]
            or query.get("pageKey")!=[identity.get("product_id")]
            or query.get("itemId")!=[identity.get("item_id")]):
        raise ValueError("PARTNER_OR_PRODUCT_ID_MISMATCH")
    return {**a, "product_id":identity["product_id"],"item_id":identity["item_id"]}

def run_once():
    result={"status":"NOT_STARTED","source_message_id":None,"submit_attempted":False,
            "post_url":None,"posted_verified":False,"affiliate_href_full":False,
            "photo_visible":False,"footer_verified":False}
    if not (os.getenv("GITHUB_ACTIONS")=="true"
            and os.getenv("GITHUB_EVENT_NAME")=="push"
            and os.getenv("GITHUB_REF_NAME")=="test/daangn-new-format-one-post-20261101"
            and os.getenv("GITHUB_RUN_ATTEMPT")=="1"
            and os.getenv("ALLOW_PINNED_POST_94743")=="true"):
        return {**result,"status":"NOT_AUTHORIZED_ONE_TIME_RUN"}
    try:
        article=article_from_report(Path("one-post-draft"))
        result["source_message_id"]=article["source_id"]
        state=parse_daangn_storage_state(os.environ["DAANGN_CAFE_STORAGE_STATE_JSON"])
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(storage_state=state,locale="ko-KR",
                                             viewport={"width":1366,"height":950})
                page=context.new_page()
                response=page.goto(TARGET,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(2000)
                if (not response or response.status!=200 or
                        urlsplit(page.url).path!=PREFIX+"new"):
                    raise RuntimeError("EXPECTED_EDITOR_NOT_REACHED")
                board=page.locator("select")
                if board.count()!=1:raise RuntimeError("BOARD_AMBIGUOUS")
                board.select_option(label=BOARD)
                if board.locator("option:checked").inner_text()!=BOARD:
                    raise RuntimeError("BOARD_NOT_HOTDEAL")
                editor=page.locator("div.ProseMirror[contenteditable=true]")
                if editor.count()!=1:raise RuntimeError("EDITOR_NOT_UNIQUE")
                image_button=page.get_by_role("button",name="이미지 추가")
                if image_button.count()!=1:raise RuntimeError("IMAGE_CONTROL_NOT_UNIQUE")
                with page.expect_file_chooser(timeout=11000) as choose:
                    image_button.click(timeout=6000)
                choose.value.set_files(str(article["image"]))
                image=editor.locator("img")
                image.first.wait_for(state="visible",timeout=18000)
                page.wait_for_function("""() => {
                   let e=document.querySelector('div.ProseMirror[contenteditable=true] img');
                   return !!e&&e.complete&&e.naturalWidth>0;
                }""",timeout=18000)
                if image.count()!=1 or image.first.evaluate("(e)=>e.naturalWidth")<=0:
                    raise RuntimeError("PHOTO_NOT_LOADED_BEFORE_TEXT")
                result["photo_uploaded_first"]=True
                # Append to the empty paragraph after the image, never editor.fill().
                editor.click()
                editor.press("ControlOrMeta+End")
                editor.press("Enter")
                lines=article["body"].split("\n")
                for i,line in enumerate(lines):
                    if line:page.keyboard.insert_text(line)
                    if i<len(lines)-1:page.keyboard.press("Enter")
                page.keyboard.press("Enter")
                title=page.locator('input[placeholder="제목을 입력해주세요."]')
                if title.count()!=1:raise RuntimeError("TITLE_NOT_UNIQUE")
                title.fill(article["title"])
                page.wait_for_timeout(1300)
                body=editor.inner_text().strip()
                result["title_exact_match"]=title.input_value()==article["title"]
                result["body_normalized_match"]=norm(body)==norm(article["body"])
                result["expected_body_chars"]=len(article["body"])
                result["observed_body_chars"]=len(body)
                if not result["body_normalized_match"]:
                    expected_compact=norm(article["body"])
                    actual_compact=norm(body)
                    at=next((i for i,(a,b) in enumerate(zip(expected_compact,actual_compact)) if a!=b),
                            min(len(expected_compact),len(actual_compact)))
                    result["first_mismatch_index"]=at
                    result["expected_around_mismatch"]=expected_compact[max(0,at-35):at+100]
                    result["actual_around_mismatch"]=actual_compact[max(0,at-35):at+100]
                if not result["title_exact_match"] or not result["body_normalized_match"]:
                    raise RuntimeError("BODY_OR_TITLE_MISMATCH")
                if not editor.evaluate("""(e,url)=>{
                    const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
                    let n;while((n=walker.nextNode())){
                      if(n.textContent.includes(url))return true;
                    }return false;
                }""",article["candidate_url"]):
                    raise RuntimeError("URL_NOT_IN_ONE_TEXT_NODE")
                if (image.count()!=1 or image.first.evaluate("(e)=>e.naturalWidth")<=0
                        or board.locator("option:checked").inner_text()!=BOARD):
                    raise RuntimeError("IMAGE_OR_BOARD_CHANGED")
                if not body.endswith(DISCLOSURE):
                    raise RuntimeError("DISCLOSURE_NOT_LAST")
                result["before_post_verified"]=True
                page.screenshot(path=str(OUT/"before_submit.png"),timeout=10000)
                if os.getenv("ONEPOST_DRY_RUN")=="true":
                    result["status"]="STAGING_VERIFIED_NO_PUBLISH"
                    return result

                submit=page.locator("button.w-full").filter(has_text=re.compile(r"^글쓰기$"))
                if submit.count()!=1 or not submit.is_enabled():
                    raise RuntimeError("SUBMIT_NOT_UNIQUE")
                result["submit_attempted"]=True
                submit.click(timeout=10000)
                result["submit_clicked_once"]=True
                page.wait_for_timeout(7500)
                final=urlsplit(page.url)
                if (final.hostname!="cafe.daangn.com" or not final.path.startswith(PREFIX)
                        or final.path==PREFIX+"new"):
                    result["status"]="PUBLISH_UNCERTAIN_NEVER_RETRY"
                    return result
                result["post_url"]=page.url
                body_now=page.locator("body").inner_text(timeout=5000)
                result["posted_verified"]=article["title"] in body_now and "오늘의 핫딜" in body_now
                hrefs=page.locator('a[href*="/re/AFFSDP"]').evaluate_all(
                    "(nodes)=>nodes.map(x=>x.getAttribute('href'))")
                result["affiliate_href_full"]=(len(hrefs)==1 and hrefs[0]==article["candidate_url"]
                    and parse_qs(urlsplit(hrefs[0]).query).get("itemId")==[article["item_id"]])
                result["photo_visible"]=page.locator('img[alt^="Post image"]').evaluate_all(
                    "(nodes)=>nodes.some(e=>e.complete&&e.naturalWidth>0&&e.naturalHeight>0)")
                result["footer_verified"]=(
                    "※ 실시간 핫딜 특성상" in body_now
                    and "카페 가입 알림 설정하고 꿀템 챙겨가세요." in body_now
                    and DISCLOSURE in body_now)
                result["status"]=("POST_PUBLISHED_AND_FORMAT_VERIFIED"
                    if all([result["posted_verified"],result["affiliate_href_full"],
                            result["photo_visible"],result["footer_verified"]])
                    else "PUBLISHED_BUT_VERIFICATION_FAILED_NEVER_RETRY")
                page.screenshot(path=str(OUT/"after_submit.png"),timeout=10000)
            finally:
                browser.close()
    except Exception as e:
        result["status"]=("PUBLISH_UNCERTAIN_NEVER_RETRY" if result["submit_attempted"]
                          else "ABORTED_BEFORE_PUBLISH")
        result["error_type"]=type(e).__name__
        result["error_reason"]=str(e)[:120] if isinstance(e,(ValueError,RuntimeError)) else "BROWSER_ERROR"
    return result

if __name__=="__main__":
    result=run_once()
    (OUT/"publish_result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("DAANGN_NEW_FORMAT_TEST "+json.dumps(result,ensure_ascii=False))
    if result["status"]!="POST_PUBLISHED_AND_FORMAT_VERIFIED":
        raise SystemExit(2)
