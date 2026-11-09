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
    if not isinstance(article.get("title"), str) or not article["title"].startswith("[쿠팡🔥핫딜] "):
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
            frame, title, body_element = get_editor_frame(page)
            if not section_empty(frame, "title") or not section_empty(frame, "body"):
                raise RuntimeError("NAVER_EDITOR_NOT_EMPTY")
            title.click(timeout=8000)
            page.keyboard.insert_text(article["title"])
            page.wait_for_timeout(500)
            if article["title"] not in title.inner_text():
                raise RuntimeError("NAVER_TITLE_NOT_APPLIED")
            body_element.click(timeout=8000)
            page.keyboard.insert_text(body)
            page.wait_for_timeout(700)
            actual_body = body_element.inner_text()
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
