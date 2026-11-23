"""Dedicated two-affiliate-link SmartEditor publisher, fail closed.

Never alters the original one-link hot-deal publisher. The complete article,
both links, and editor title must be verified before the single publish click.
"""
from __future__ import annotations

from pathlib import Path
import re

from naver_blog_one_shot import safely_parse_state, get_editor_frame, publish_exactly_one
from gh_only.coupang_partners_api import issued_link_url
from gh_only.naver_blog_deal_publisher import (
    BLOG_EDITOR, section_empty, apply_verified_title, _exact_editor_title,
    _write_styled_block, upload_image, _focus_text_after_last_image,
    _affiliate_anchors,
)
from gh_only.naver_blog_jirum import NAVER_TITLE_MAX_UTF16_UNITS
from growth_lab.trend_comparison import DISCLOSURE


def _insert_exact_native_link(page, frame, url: str) -> None:
    """Add one native clickable link; never type a raw affiliate URL."""
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_timeout(550)
    para = frame.locator(".se-section-text .se-text-paragraph").last
    if not para.count() or para.inner_text().strip():
        raise RuntimeError("COMPARISON_LINK_PARAGRAPH_NOT_EMPTY")
    button = frame.locator(
        "button.se-link-toolbar-button.se-property-toolbar-custom-layer-button:visible"
    )
    if button.count() != 1:
        raise RuntimeError("COMPARISON_LINK_TOOLBAR_MISSING")
    button.click(timeout=5000)
    field = frame.locator("input.se-custom-layer-link-input:visible")
    apply = frame.locator("button.se-custom-layer-link-apply-button:visible")
    if field.count() != 1 or apply.count() != 1:
        raise RuntimeError("COMPARISON_LINK_FORM_INVALID")
    field.fill(url, timeout=7000)
    if field.input_value() != url:
        raise RuntimeError("COMPARISON_LINK_FORM_MISMATCH")
    apply.click(timeout=7000)
    page.wait_for_timeout(1000)
    if _affiliate_anchors(frame, url, url)["count"] != 1:
        raise RuntimeError("COMPARISON_LINK_NOT_CLICKABLE")


def _dismiss_alerts(page) -> None:
    page.wait_for_timeout(1800)
    for _ in range(7):
        popups = []
        for fr in page.frames:
            found = fr.locator(".se-popup.se-popup-alert-confirm:visible")
            if found.count():
                popups.append(found.first)
        if not popups:
            page.wait_for_timeout(600)
            if not any(fr.locator(".se-popup.se-popup-alert-confirm:visible").count()
                       for fr in page.frames):
                return
            continue
        for popup in popups:
            cancel = popup.locator("button.se-popup-button-cancel:visible")
            if cancel.count() != 1:
                raise RuntimeError("COMPARISON_UNKNOWN_NAVER_ALERT")
            cancel.click(timeout=3500)
        page.wait_for_timeout(450)
    raise RuntimeError("COMPARISON_REPEATED_NAVER_ALERT")


def submit_comparison(article: dict, session_json: str) -> str:
    from playwright.sync_api import sync_playwright

    title = article.get("title")
    paragraphs = article.get("paragraphs")
    links = article.get("links")
    photo = article.get("photo")
    if (not isinstance(title, str) or not title.startswith("[쿠팡📊비교] ")
            or len(title.encode("utf-16-le")) // 2 > NAVER_TITLE_MAX_UTF16_UNITS):
        raise RuntimeError("COMPARISON_TITLE_INVALID")
    if (not isinstance(paragraphs, list) or not 7 <= len(paragraphs) <= 15
            or any(not isinstance(p, str) or not 10 <= len(p) <= 900
                   or "link.coupang.com" in p for p in paragraphs)):
        raise RuntimeError("COMPARISON_BODY_INVALID")
    if (not isinstance(links, list) or len(links) != 2
            or any(not isinstance(x, dict)
                   or not issued_link_url(x.get("url"))
                   or not isinstance(x.get("label"), str)
                   or not x["label"].startswith("📦 상품 ")
                   for x in links)
            or links[0]["url"] == links[1]["url"]):
        raise RuntimeError("COMPARISON_TWO_OFFICIAL_LINKS_REQUIRED")
    if not isinstance(photo, Path) or not photo.is_file():
        raise RuntimeError("COMPARISON_PRODUCT_IMAGE_MISSING")

    state = safely_parse_state(session_json)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=state, locale="ko-KR")
            page = context.new_page()
            response = page.goto(BLOG_EDITOR, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(3300)
            if response is None or response.status >= 400:
                raise RuntimeError("COMPARISON_EDITOR_HTTP_DENIED")
            _dismiss_alerts(page)
            frame, title_field, body = get_editor_frame(page)
            if not section_empty(frame, "title") or not section_empty(frame, "body"):
                raise RuntimeError("COMPARISON_EDITOR_NOT_EMPTY")
            apply_verified_title(page, frame, title_field, title)
            body.click(timeout=8000)
            _write_styled_block(page, {"text": DISCLOSURE})
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.wait_for_timeout(450)
            upload_image(page, frame, photo)
            _focus_text_after_last_image(page, frame)

            for p in paragraphs:
                _write_styled_block(page, {"text": p})
                page.keyboard.press("Enter")
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)

            for i, link in enumerate(links):
                if i:
                    # The native link toolbar moves selection: re-focus the
                    # verified last URL paragraph and exit its active anchor.
                    tail = frame.locator(".se-section-text .se-text-paragraph").last
                    tail.click(timeout=7000)
                    page.keyboard.press("End")
                    page.keyboard.press("ArrowRight")
                    page.keyboard.press("Enter")
                    page.keyboard.press("Enter")
                _write_styled_block(page, {"text": link["label"]})
                _insert_exact_native_link(page, frame, link["url"])

            if not _exact_editor_title(frame, title):
                raise RuntimeError("COMPARISON_FINAL_TITLE_CHANGED")
            section = "\n".join(frame.locator(".se-section-text").all_inner_texts())
            if (section.count(DISCLOSURE) != 1
                    or any(p not in section for p in paragraphs)
                    or any(section.count(x["label"]) != 1 for x in links)):
                raise RuntimeError("COMPARISON_BODY_NOT_VERIFIED")
            if (any(section.count(x["url"]) != 1 for x in links)
                    or any(_affiliate_anchors(frame, x["url"], x["url"])["count"] != 1
                           for x in links)):
                raise RuntimeError("COMPARISON_FINAL_LINKS_NOT_VERIFIED")
            url_lines = [
                p.strip() for p in frame.locator(
                    ".se-section-text .se-text-paragraph").all_inner_texts()
                if "link.coupang.com" in p
            ]
            if len(url_lines) != 2 or set(url_lines) != {x["url"] for x in links}:
                raise RuntimeError("COMPARISON_LINK_STRUCTURE_INVALID")
            if frame.locator(".se-component.se-image, .se-section-image").count() < 1:
                raise RuntimeError("COMPARISON_IMAGE_NOT_PRESENT")
            receipt_id = publish_exactly_one(frame, page)
            if not re.fullmatch(r"\d+", str(receipt_id)):
                raise RuntimeError("COMPARISON_PUBLISH_RECEIPT_INVALID")
            return "https://blog.naver.com/mw5114/" + str(receipt_id)
        finally:
            browser.close()
