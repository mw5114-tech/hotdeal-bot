"""Read-only verify one actual posted Daangn Cafe test article after a URL-encoded slug.

No write UI, publishing, reposting, submissions or authenticated mutations.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from playwright.sync_api import sync_playwright
from gh_only.daangn_editor_probe import parse_daangn_storage_state

POST_URL=("https://cafe.daangn.com/i-gagyeog-mos-c/posts/"
          "%EC%98%A4%EB%8A%98%EC%9D%98-%ED%95%AB%EB%94%9C-"
          "%ED%95%84%EB%A6%BD%EC%8A%A4-%EB%B8%94%EB%A0%8C%EB%8D%94-"
          "3000-%EC%8B%9C%EB%A6%AC%EC%A6%88-P213erWA")
OUT=Path("daangn-verify-once");OUT.mkdir(exist_ok=True)
def main():
    result={"attempted_posting":False,"post_reposted":False,"url":POST_URL,
            "post_visible":False,"status":"NOT_STARTED"}
    try:
        state=parse_daangn_storage_state(os.environ["DAANGN_CAFE_STORAGE_STATE_JSON"])
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                ctx=browser.new_context(storage_state=state,locale="ko-KR",
                                        viewport={"width":1366,"height":1000})
                page=ctx.new_page()
                resp=page.goto(POST_URL,wait_until="domcontentloaded",timeout=30000)
                page.wait_for_timeout(5200)
                path=urlsplit(page.url).path
                decoded=unquote(path)
                result["http_status"]=resp.status if resp else None
                result["loaded_post_path"]=path
                result["decoded_path"]=decoded
                result["same_requested_post"]=(path==urlsplit(POST_URL).path)
                body=page.locator("body").inner_text()
                # Product and price were verified before clicking.
                result["has_product_name"]="필립스 블렌더 3000 시리즈" in body
                result["has_sale_price"]="34,160원" in body
                result["has_owner_tag"]="AF9668915" in body or page.locator('a[href*="lptag=AF9668915"]').count()>0
                result["has_our_AFFSDP"]="/re/AFFSDP" in body or page.locator('a[href*="/re/AFFSDP"]').count()>0
                result["has_disclosure"]="쿠팡 파트너스 활동의 일환" in body
                result["has_hotdeal_board"]="오늘의 핫딜" in body
                result["img_count"]=page.locator("img").count()
                result["image_candidate_in_post"]=page.locator("article img, [data-testid*=post] img").count()
                result["image_geometry"]=page.evaluate("""() => [...document.images].map((img)=>({
                    width: img.naturalWidth, height: img.naturalHeight,
                    renderedWidth:Math.round(img.getBoundingClientRect().width),
                    renderedHeight:Math.round(img.getBoundingClientRect().height),
                    alt:(img.alt||'').slice(0,70),
                    sourceHost:(()=>{try{return new URL(img.currentSrc||img.src).hostname}catch{return ''}})(),
                    inRichContent:!!img.closest('.ProseMirror,.tiptap,[class*=postContent],[class*=PostContent]')
                })).slice(0,28)""")
                result["page_h1"]=[x.strip()[:100] for x in page.locator("h1").all_inner_texts()][:4]
                result["text_tail"]=body[-580:]
                result["post_visible"]=(result["http_status"]==200
                                        and result["same_requested_post"]
                                        and result["has_product_name"]
                                        and result["has_sale_price"]
                                        and result["has_owner_tag"]
                                        and result["has_disclosure"])
                result["status"]="POST_VERIFIED" if result["post_visible"] else "POST_VERIFICATION_INCOMPLETE"
                page.screenshot(path=str(OUT/"post_detail_verified.png"),full_page=False,timeout=10000)
            finally:
                browser.close()
    except Exception as e:
        result["status"]="ERROR"
        result["error_type"]=type(e).__name__
    (OUT/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("DAANGN_POST_READ_ONLY_VERIFY "+json.dumps(result,ensure_ascii=False))
    if result["status"]!="POST_VERIFIED":
        raise SystemExit(2)

if __name__=="__main__":
    main()
