"""Read-only audit for the already-published Jirum 94744 Daangn post.

Never clicks any submit/edit/delete button, never writes state.
The log contains checks and counts only, not account/session data.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

from gh_only.daangn_editor_probe import DISCLOSURE, parse_daangn_storage_state

POST_URL = (
    "https://cafe.daangn.com/i-gagyeog-mos-c/posts/"
    "%EC%98%A4%EB%8A%98%EC%9D%98-%ED%95%AB%EB%94%9C-Apple-2024-"
    "%EC%95%84%EC%9D%B4%ED%8C%A8%EB%93%9C-miniA17-Pro-%EB%AA%A8%EB%8D%B8-"
    "dZnYZrNd?from=write"
)
EXPECTED_LINK = (
    "https://link.coupang.com/re/AFFSDP?"
    "lptag=AF9668915&pageKey=8409598558&itemId=24314362699"
    "&vendorItemId=91330172860"
)
TITLE_MARKER = "아이패드 mini"


def main() -> None:
    from playwright.sync_api import sync_playwright

    state = parse_daangn_storage_state(
        os.environ.get("DAANGN_CAFE_STORAGE_STATE_JSON", "")
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                storage_state=state, locale="ko-KR",
                viewport={"width": 1366, "height": 950},
            )
            page = context.new_page()
            response = page.goto(
                POST_URL, wait_until="domcontentloaded", timeout=30000
            )
            page.wait_for_timeout(2700)
            body = page.locator("body").inner_text(timeout=12000)
            anchors = page.locator("a").evaluate_all(
                "(els) => els.map(e => e.href)"
            )
            imgs = page.locator("img").evaluate_all(
                """(els) => els.map(e => ({
                   altIsPostImage: (e.alt||'').startsWith('Post image'),
                   width: e.naturalWidth, height: e.naturalHeight,
                   loaded: e.complete,
                   displayed: e.getBoundingClientRect().width > 0
                 }))"""
            )
            # Read-only screenshot and generic image metadata for site markup diagnosis.
            # Do not capture or serialize browser storage/cookies.
            page.screenshot(
                path="daangn-94744-post-readonly.png",
                full_page=True,
                timeout=12000,
            )
            image_metadata = page.locator("img").evaluate_all(
                """(els)=>els.map(e=>({
                   width:e.naturalWidth,height:e.naturalHeight,
                   alt:(e.alt||'').slice(0,60),
                   top:Math.round(e.getBoundingClientRect().top),
                   host:(()=>{try{return new URL(e.currentSrc||e.src).hostname}catch{return ''}})(),
                   srcSuffix:(()=>{try{return new URL(e.currentSrc||e.src).pathname.split('/').pop().slice(0,55)}catch{return ''}})()
                 })).filter(x=>x.width>=150&&x.height>=150).slice(0,25)"""
            )
            print("DAANGN_READONLY_IMAGE_METADATA " + json.dumps(
                image_metadata, ensure_ascii=False
            ))
            parsed = urlsplit(page.url)
            expected_post_path = urlsplit(POST_URL).path
            checks = {
                "http_status": response.status if response else None,
                "final_is_expected_post": (
                    parsed.hostname == "cafe.daangn.com"
                    and parsed.path == expected_post_path
                ),
                "title_visible": TITLE_MARKER in body,
                "board_visible": "오늘의 핫딜" in body,
                "body_affiliate_url_count": body.count(EXPECTED_LINK),
                "exact_anchor_count": sum(
                    href == EXPECTED_LINK for href in anchors
                ),
                "affsdp_anchor_count": sum(
                    "/re/AFFSDP" in href for href in anchors
                ),
                "post_image_alt_count": sum(
                    row["altIsPostImage"] for row in imgs
                ),
                "loaded_post_image_count": sum(
                    row["altIsPostImage"] and row["loaded"]
                    and row["width"] > 0 and row["height"] > 0
                    for row in imgs
                ),
                "visible_large_image_count": sum(
                    row["loaded"] and row["displayed"]
                    and row["width"] >= 150 and row["height"] >= 150
                    for row in imgs
                ),
                "stock_notice_visible": "※ 실시간 핫딜 특성상" in body,
                "join_notice_visible": (
                    "카페 가입 알림 설정하고 꿀템 챙겨가세요." in body
                ),
                "disclosure_visible": DISCLOSURE in body,
                "body_length": len(body),
            }
            print("DAANGN_READONLY_POST_AUDIT " + json.dumps(
                checks, ensure_ascii=False, sort_keys=True
            ))
            if not checks["final_is_expected_post"]:
                raise RuntimeError("READONLY_AUDIT_POST_NOT_REACHED")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
