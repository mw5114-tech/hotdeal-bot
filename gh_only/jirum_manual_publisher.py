"""One Daangn publish attempt using the photo-first flow already verified live.

Caller is responsible for durable pre-click claim. This module never retries a
submit and does not mutate the queue. It returns verification evidence only.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from gh_only.daangn_editor_probe import parse_daangn_storage_state, DISCLOSURE

TARGET = "https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
PREFIX = "/i-gagyeog-mos-c/posts/"
BOARD = "🔥 오늘의 핫딜"


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def validate_editor_copy(editor, body: str, url: str, image) -> bool:
    observed = editor.inner_text().strip()
    exact = norm(observed) == norm(body)
    if not exact:
        # Daangn injects ONLY this temporary label inside photo upload UI.
        label = editor.evaluate("""(e) => {
          const img=e.querySelector('img');
          if(!img) return '';
          let n=img.parentElement;
          for(let i=0;i<4 && n && n!==e;i++,n=n.parentElement){
            if((n.innerText||'').includes('준비중'))return '준비중';
          }
          return '';
        }""")
        exact = norm(observed) == "준비중" + norm(body) and label == "준비중"
    url_one_node = editor.evaluate("""(e,url) => {
      const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
      let n;while((n=walker.nextNode())){
        if(n.textContent.includes(url))return true;
      }
      return false;
    }""", url)
    return bool(
        exact
        and observed.count(url) == 1
        and observed.rstrip().endswith(DISCLOSURE)
        and url_one_node
        and image.count() == 1
        and image.first.evaluate("(e)=>e.complete&&e.naturalWidth>0")
    )


def publish_one(article: dict, state_secret: str | None = None) -> dict:
    """Attempt exactly one submission. Caller already persisted claim."""
    import os
    from playwright.sync_api import sync_playwright

    result = {
        "status": "NOT_STARTED",
        "source_id": article["source_id"],
        "submit_attempted": False,
        "post_url": None,
        "photo_uploaded_first": False,
    }
    try:
        state = parse_daangn_storage_state(
            state_secret if state_secret is not None else os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON", "")
        )
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    storage_state=state, locale="ko-KR",
                    viewport={"width": 1366, "height": 950},
                )
                page = ctx.new_page()
                resp = page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1800)
                if (not resp or resp.status != 200
                    or urlsplit(page.url).hostname != "cafe.daangn.com"
                    or urlsplit(page.url).path != PREFIX + "new"):
                    raise RuntimeError("EXPECTED_EDITOR_NOT_REACHED")
                board = page.locator("select")
                if board.count() != 1:
                    raise RuntimeError("BOARD_NOT_UNIQUE")
                board.select_option(label=BOARD)
                if board.locator("option:checked").inner_text().strip() != BOARD:
                    raise RuntimeError("WRONG_BOARD")
                editor = page.locator("div.ProseMirror[contenteditable=true]")
                if editor.count() != 1:
                    raise RuntimeError("EDITOR_NOT_UNIQUE")
                photo = article.get("image")
                if photo is None or not photo.is_file():
                    raise RuntimeError("PRODUCT_PHOTO_NOT_AVAILABLE")
                add_image = page.get_by_role("button", name="이미지 추가")
                if add_image.count() != 1:
                    raise RuntimeError("IMAGE_UPLOAD_BUTTON_NOT_UNIQUE")
                with page.expect_file_chooser(timeout=11000) as chooser:
                    add_image.click(timeout=7000)
                chooser.value.set_files(str(photo))
                image = editor.locator("img")
                image.first.wait_for(state="visible", timeout=18000)
                page.wait_for_function("""() => {
                  const img=document.querySelector('div.ProseMirror[contenteditable=true] img');
                  return !!img && img.complete && img.naturalWidth > 0;
                }""", timeout=18000)
                if image.count() != 1:
                    raise RuntimeError("IMAGE_AMBIGUOUS")
                result["photo_uploaded_first"] = True
                # The successful live test put photo FIRST then appended text.
                editor.click()
                editor.press("ControlOrMeta+End")
                editor.press("Enter")
                lines = article["body"].split("\n")
                for i, line in enumerate(lines):
                    if line:
                        page.keyboard.insert_text(line)
                    if i < len(lines) - 1:
                        page.keyboard.press("Enter")
                page.keyboard.press("Enter")
                title_input = page.locator('input[placeholder="제목을 입력해주세요."]')
                if title_input.count() != 1:
                    raise RuntimeError("TITLE_NOT_UNIQUE")
                title_input.fill(article["title"])
                page.wait_for_timeout(1200)
                if title_input.input_value() != article["title"]:
                    raise RuntimeError("TITLE_NOT_VERIFIED")
                if not validate_editor_copy(
                    editor, article["body"], article["candidate_url"], image
                ):
                    raise RuntimeError("EDITOR_CONTENT_OR_FULL_URL_NOT_VERIFIED")
                if board.locator("option:checked").inner_text().strip() != BOARD:
                    raise RuntimeError("BOARD_RESET")
                submit = page.locator("button.w-full").filter(has_text=re.compile(r"^글쓰기$"))
                if submit.count() != 1 or not submit.is_enabled():
                    raise RuntimeError("SUBMIT_NOT_UNIQUE")
                # No retry under ANY condition, including timeout or navigation error.
                result["submit_attempted"] = True
                submit.click(timeout=10000)
                page.wait_for_timeout(7500)
                dest = urlsplit(page.url)
                result["post_url"] = page.url if (
                    dest.scheme == "https"
                    and dest.hostname == "cafe.daangn.com"
                    and dest.path.startswith(PREFIX)
                    and dest.path != PREFIX + "new"
                ) else None
                if not result["post_url"]:
                    result["status"] = "PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                    return result
                content = page.locator("body").inner_text(timeout=5000)
                hrefs = page.locator('a[href*="/re/AFFSDP"]').evaluate_all(
                    "(nodes)=>nodes.map(n=>n.getAttribute('href'))"
                )
                same_link = (len(hrefs) == 1
                             and hrefs[0] == article["candidate_url"]
                             and parse_qs(urlsplit(hrefs[0]).query).get("itemId")
                             == [article["item_id"]])
                photo_visible = page.locator('img[alt^="Post image"]').evaluate_all(
                    "(nodes)=>nodes.some(e=>e.complete&&e.naturalWidth>0&&e.naturalHeight>0)"
                )
                footer = all(term in content for term in (
                    "※ 실시간 핫딜 특성상",
                    "카페 가입 알림 설정하고 꿀템 챙겨가세요.",
                    DISCLOSURE,
                ))
                title_ok = article["title"] in content
                board_ok = "오늘의 핫딜" in content
                result.update({
                    "affiliate_href_full": same_link,
                    "photo_visible": photo_visible,
                    "footer_verified": footer,
                    "title_visible": title_ok,
                    "board_verified": board_ok,
                })
                result["status"] = (
                    "POST_PUBLISHED_AND_FORMAT_VERIFIED"
                    if all((same_link, photo_visible, footer, title_ok, board_ok))
                    else "PUBLISHED_BUT_FORMAT_UNVERIFIED_DO_NOT_RETRY"
                )
            finally:
                browser.close()
    except Exception as exc:
        result["status"] = ("PUBLISH_UNCERTAIN_DO_NOT_RETRY"
                            if result["submit_attempted"] else "ABORTED_BEFORE_SUBMIT")
        result["error_type"] = type(exc).__name__
        if isinstance(exc, (RuntimeError, ValueError)):
            result["error_reason"] = str(exc)[:130]
    return result
