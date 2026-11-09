"""Single Naver Blog SmartEditor attempt using owner's existing login session.

This module cannot mint links, bypass authentication or retry an uncertain
publish. Upload a verified local product image before the submit button.
"""
from __future__ import annotations

from pathlib import Path

from naver_blog_one_shot import (
    get_editor_frame, publish_exactly_one, safely_parse_state,
)

BLOG_EDITOR = "https://blog.naver.com/PostWriteForm.naver?blogId=mw5114"


def section_empty(frame, name: str) -> bool:
    selector = (".se-section-documentTitle" if name == "title"
                else ".se-section-text") + " .se-module.se-is-empty"
    return frame.locator(selector).count() == 1


def upload_image(page, frame, image: Path):
    if not image.is_file() or image.suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".webp"
    } or image.stat().st_size > 2_500_000:
        raise RuntimeError("NAVER_IMAGE_INVALID")
    photo_buttons = (
        frame.get_by_role("button", name="사진", exact=True),
        frame.locator("button.se-image-toolbar-button"),
        frame.locator("button.se-toolbar-button-image"),
    )
    image_button = next(
        (loc.first for loc in photo_buttons
         if loc.count() == 1 and loc.first.is_visible()), None
    )
    if image_button is None:
        print("NAVER_PHOTO_BUTTON_NOT_FOUND")
        # Only safe selector diagnostics, no page content.
        try:
            print("NAVER_PHOTO_TOOLBAR_CANDIDATES=" +
                  str(frame.locator("button").count()))
            print("NAVER_IMAGE_FILE_INPUTS=" +
                  str(frame.locator("input[type=file]").count()))
        except Exception:
            pass
        raise RuntimeError("NAVER_IMAGE_TOOLBAR_UNVERIFIED")
    try:
        with page.expect_file_chooser(timeout=10000) as chooser:
            image_button.click(timeout=8000)
        chooser.value.set_files(str(image.resolve()))
    except Exception:
        raise RuntimeError("NAVER_IMAGE_UPLOAD_CHOOSER_FAILED")
    page.wait_for_timeout(2600)
    inserted = (
        frame.locator(".se-component-image").count()
        + frame.locator(".se-section-image").count()
    )
    if inserted == 0:
        raise RuntimeError("NAVER_IMAGE_UPLOAD_NOT_VERIFIED")
    print("NAVER_PRODUCT_PHOTO_INSERTED")


def submit_one(article: dict, session_json: str, *, publish: bool) -> str | None:
    from playwright.sync_api import sync_playwright

    state = safely_parse_state(session_json)
    if not isinstance(article.get("title"), str) or not (
        article["title"].startswith("[쿠팡🔥핫딜] ")
        or (not publish and article["title"] == "[자동화 테스트] 네이버 블로그 글쓰기 연결 확인")
    ):
        raise RuntimeError("NAVER_DEAL_TITLE_INVALID")
    body = article.get("body")
    if (not isinstance(body, str) or len(body) > 9000
            or not body.startswith("이 포스팅은 쿠팡 파트너스")
            or body.count(article.get("link", "")) != 1):
        raise RuntimeError("NAVER_DISCLOSURE_OR_LINK_INVALID")
    image = article.get("image")
    if not isinstance(image, Path):
        raise RuntimeError("NAVER_PHOTO_REQUIRED")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=state, locale="ko-KR")
            page = context.new_page()
            response = page.goto(BLOG_EDITOR, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3300)
            if not response or response.status >= 400:
                raise RuntimeError("NAVER_EDITOR_HTTP_DENIED")
            # Match the proven Naver one-shot writer: close the editor's
            # previous-draft popup before attempting to focus the title.
            for current_frame in page.frames:
                try:
                    cancel = current_frame.locator("button.se-popup-button-cancel")
                    if cancel.count() == 1 and cancel.is_visible():
                        cancel.click(timeout=4000)
                        print("NAVER_EDITOR_RESTORE_POPUP_DISMISSED")
                except Exception:
                    pass
            page.wait_for_timeout(700)
            frame, title, body_element = get_editor_frame(page)
            if not section_empty(frame, "title") or not section_empty(frame, "body"):
                raise RuntimeError("NAVER_EDITOR_NOT_EMPTY")
            title.click(timeout=8000)
            page.keyboard.insert_text(article["title"])
            page.wait_for_timeout(500)
            actual_title = frame.locator(".se-section-documentTitle").inner_text()
            print("NAVER_TITLE_CHECK " + str({
                "expected_chars": len(article["title"]),
                "actual_chars": len(actual_title),
                "exact": article["title"] in actual_title,
                "product_tail": article["title"][-12:] in actual_title,
            }))
            if article["title"] not in actual_title:
                # A fresh SmartEditor can discard an insertText when the title
                # field only had the active placeholder. One alternate keypath
                # is safe ONLY while the title module is still marked empty.
                body_now = frame.locator(".se-section-text").inner_text()
                print("NAVER_TITLE_FOCUS_CHECK " + str({
                    "empty": section_empty(frame, "title"),
                    "body_has_title": article["title"] in body_now,
                    "body_chars": len(body_now),
                }))
                if section_empty(frame, "title") and article["title"] not in body_now:
                    title.click(timeout=8000)
                    page.keyboard.type(article["title"], delay=16)
                    page.wait_for_timeout(900)
                    actual_title = frame.locator(".se-section-documentTitle").inner_text()
                    print("NAVER_TITLE_FALLBACK " + str({
                        "actual_chars": len(actual_title),
                        "exact": article["title"] in actual_title,
                    }))
                if article["title"] not in actual_title:
                    raise RuntimeError("NAVER_TITLE_NOT_APPLIED")
            body_element.click(timeout=8000)
            page.keyboard.insert_text(body)
            page.wait_for_timeout(700)
            actual_body = frame.locator(".se-section-text").inner_text()
            if (body[:32] not in actual_body
                    or article["link"] not in actual_body
                    or actual_body.count(article["link"]) != 1):
                raise RuntimeError("NAVER_BODY_NOT_APPLIED")
            upload_image(page, frame, image)
            if not publish:
                print("NAVER_IMAGE_AND_TEXT_DRAFT_OK_NO_PUBLISH")
                return None
            number = publish_exactly_one(frame, page)
            result = f"https://blog.naver.com/mw5114/{number}"
            print("NAVER_ONE_POST_URL=" + result)
            return result
        finally:
            browser.close()
