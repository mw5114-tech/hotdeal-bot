"""Try Jigeumsa's PUBLIC link-search UI with one exact Coupang product/item URL.

One user-facing interaction only; no hidden endpoints, login or posting.
Records a brief diagnostic after submitting the product URL.
"""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit
import json

HOME="https://jigeumsa.com/"
PRODUCT="https://www.coupang.com/vp/products/6221369038?itemId=12453073752"
MARKERS=("핫케이크", "가격조회", "가격 이력", "평균가", "평균", "가격 추이",
         "상품을 찾을 수", "결과가 없", "가격 기록", "최저가")

def run():
    result={
        "source":"jigeumsa",
        "source_product_id":"6221369038",
        "source_item_id":"12453073752",
        "state":"NOT_RUN",
        "submitted":False,
        "exact_option_price_verified":False,
        "posts_published":0,
    }
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(locale="ko-KR")
                page=context.new_page()
                page.goto(HOME,wait_until="domcontentloaded",timeout=25000)
                if urlsplit(page.url).hostname not in ("jigeumsa.com","www.jigeumsa.com"):
                    result["state"]="UNEXPECTED_REDIRECT"
                    return result
                inputs=page.locator('input[placeholder*="쿠팡 상품 URL"]')
                if inputs.count()==0:
                    result["state"]="PRODUCT_URL_FIELD_NOT_FOUND"
                    return result
                inputs.first.fill(PRODUCT)
                button=page.get_by_role("button",name="가격조회",exact=True)
                if button.count()==0:
                    result["state"]="PRICE_LOOKUP_BUTTON_NOT_FOUND"
                    return result
                button.first.click(timeout=7000)
                result["submitted"]=True
                try:
                    page.wait_for_load_state("networkidle",timeout=13000)
                except PWTimeout:
                    pass
                # Wait for modal/data to render without retries.
                page.wait_for_timeout(1200)
                result["final_host"]=urlsplit(page.url).hostname
                result["page_title"]=page.title()[:140]
                text=" ".join(page.locator("body").inner_text(timeout=7000).split())
                excerpts=[]
                for marker in MARKERS:
                    n=text.find(marker)
                    if n!=-1:
                        excerpts.append({
                            "marker":marker,
                            "excerpt":text[max(0,n-65):n+160]
                        })
                result["visible_evidence"]=excerpts[:9]
                result["item_id_visible"]="12453073752" in text
                result["product_id_visible"]="6221369038" in text
                result["state"]="SUBMITTED_UI_RESULTS_VISIBLE" if excerpts else "SUBMITTED_BUT_NO_VISIBLE_PRICE_MATCH"
            finally:
                browser.close()
    except Exception as exc:
        result["state"]="UI_PROBE_ERROR"
        result["error_type"]=type(exc).__name__
    return result

if __name__=="__main__":
    result=run()
    Path("jigeumsa_link_lookup_diagnostic.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("JIGEUMSA_LINK_UI_PROBE "+json.dumps(result,ensure_ascii=False))
