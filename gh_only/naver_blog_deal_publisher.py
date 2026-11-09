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


def enter_text_in_chunks(page, content: str, chunk_size: int = 20):
    """SmartEditor accepts small native insertText events more reliably."""
    for offset in range(0, len(content), chunk_size):
        page.keyboard.insert_text(content[offset:offset+chunk_size])
        page.wait_for_timeout(90)



def _write_styled_block(page, item: dict) -> None:
    """Enter a complete Korean text block without interrupting IME composition.

    Switching Ctrl+B during a Korean label can DROP text in SmartEditor;
    actual bold is applied to already-composed paragraphs afterwards.
    """
    # A new SmartEditor paragraph initially accepts only the first emoji/
    # character if a long insertText lands while its selection model mounts.
    # Prime one character, let it settle, then type the rest as native keys.
    content = item["text"]
    page.keyboard.insert_text(content[:1])
    page.wait_for_timeout(550)
    page.keyboard.type(content[1:], delay=22)
    page.wait_for_timeout(260)


def _bold_existing_labels(page, frame, blocks: list[dict]) -> int:
    """Style existing complete paragraphs using Naver's Ctrl+B shortcut."""
    count = 0
    for item in blocks:
        label = item.get("bold_prefix", "")
        if not label:
            continue
        paragraph = frame.locator(".se-section-text .se-text-paragraph").filter(
            has_text=item["text"]
        )
        if paragraph.count() != 1 or not paragraph.first.is_visible():
            print("NAVER_BOLD_PARAGRAPH_UNAVAILABLE " + str(item.get("kind")))
            continue
        paragraph.first.select_text(timeout=5000)
        page.keyboard.press("Control+b")
        page.wait_for_timeout(170)
        count += 1
    print("NAVER_BOLD_LABEL_PARAGRAPHS_APPLIED=" + str(count))
    return count

def _editor_layout(frame, phase: str) -> None:
    try:
        components = frame.locator(".se-component").evaluate_all("""
            els => els.slice(0,26).map(e => ({
                kind: (e.className || '').slice(0,120),
                editables: e.querySelectorAll('[contenteditable]').length,
                textLength: (e.innerText || '').length
            }))
        """)
        print("NAVER_LAYOUT_" + phase + " " + str(components))
    except Exception:
        print("NAVER_LAYOUT_" + phase + "_UNAVAILABLE")


def _photo_between_disclosure_and_price(frame, disclosure: str) -> bool:
    return frame.evaluate("""
        (disclosure) => {
            const root = document;
            const ps = Array.from(root.querySelectorAll('.se-text-paragraph'));
            const intro = ps.find(e => (e.innerText || '').includes(disclosure));
            const price = ps.find(e => (e.innerText || '').includes('오늘 특가'));
            const img = root.querySelector('.se-component.se-image, .se-section-image');
            if (!intro || !price || !img) return false;
            const follows = (left, right) =>
                (left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
            return follows(intro, img) && follows(img, price);
        }
    """, disclosure)


def render_readable_blocks(page, frame, body_element, article: dict) -> None:
    """Native paragraphs + bold labels + photo after disclosure, no HTML paste.

    The editor is verified empty before calling; any uncertain focus/order
    causes a hard stop before the publish button is reached.
    """
    from gh_only.naver_blog_format import validate_blocks
    blocks = article["blocks"]
    validate_blocks(blocks, article["link"])
    body_element.click(timeout=8000)
    _write_styled_block(page, blocks[0])
    page.wait_for_timeout(350)
    # Separate disclosure into its own paragraph and leave a blank paragraph.
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_timeout(450)
    upload_image(page, frame, article["image"])
    _editor_layout(frame, "AFTER_PHOTO")

    # New SmartEditor text component after the inserted photo. Only accept a
    # text component actually ordered after the photo, not the intro above it.
    next_text = frame.locator(
        ".se-component.se-image ~ .se-component.se-text .se-text-paragraph"
    )
    if next_text.count():
        candidate = next_text.last
        candidate.click(timeout=7000)
    else:
        # Enter on the image's trailing boundary may create the next text block.
        image = frame.locator(".se-section-image").last
        if not image.count():
            raise RuntimeError("NAVER_IMAGE_COMPONENT_NOT_FOUND")
        image.click(timeout=7000)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(450)
        next_text = frame.locator(
            ".se-component.se-image ~ .se-component.se-text .se-text-paragraph"
        )
        if next_text.count() == 0:
            raise RuntimeError("NAVER_TEXT_AFTER_PHOTO_NOT_FOUND")
        next_text.last.click(timeout=7000)

    written = []
    for index, item in enumerate(blocks[2:]):
        if item.get("type") != "text":
            raise RuntimeError("NAVER_UNEXPECTED_BLOCK_TYPE")
        if index:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.wait_for_timeout(450)
        _write_styled_block(page, item)
        written.append(item["text"])
        page.wait_for_timeout(90)

    page.wait_for_timeout(600)
    bold_applied = _bold_existing_labels(page, frame, blocks)
    page.wait_for_timeout(550)
    section = "\n".join(frame.locator(".se-section-text").all_inner_texts())
    conditions = {
        "disclosure": blocks[0]["text"] in section,
        "sale": "오늘 특가" in section,
        "link": section.count(article["link"]) == 1,
        "closing_note": blocks[-1]["text"] in section,
        "paragraphs": frame.locator(".se-text-paragraph").count(),
        "bold_applied": bold_applied,
        "bold_segments": frame.locator(
            ".se-section-text strong, .se-section-text b, "
            ".se-section-text span[style*='font-weight: bold'], "
            ".se-section-text .se-text-format-bold"
        ).count(),
        "photo_between": _photo_between_disclosure_and_price(frame, blocks[0]["text"]),
    }
    print("NAVER_FORMAT_CHECK " + str(conditions))
    if not conditions["sale"] or not conditions["photo_between"]:
        # The price/label copy is intentionally public; do not log full URLs.
        snippets = []
        for i, para in enumerate(frame.locator(".se-text-paragraph").all_inner_texts()[:28]):
            stripped = para.replace(article["link"], "[AFFILIATE_LINK]")
            snippets.append((i, stripped[:100]))
        print("NAVER_PARAGRAPH_DIAGNOSTIC " + str(snippets))
    _editor_layout(frame, "FINAL")
    if not all((conditions["disclosure"], conditions["sale"],
                conditions["link"], conditions["closing_note"],
                conditions["photo_between"])):
        raise RuntimeError("NAVER_FORMAT_STRUCTURE_NOT_VERIFIED")
    if conditions["paragraphs"] < 7:
        raise RuntimeError("NAVER_FORMAT_PARAGRAPHS_MISSING")
    if conditions["bold_applied"] < 3 or conditions["bold_segments"] < 3:
        raise RuntimeError("NAVER_FORMAT_BOLD_NOT_APPLIED")
    print("NAVER_READABLE_LAYOUT_VERIFIED")


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
            # Naver can have several hidden old-draft alerts sharing a CSS
            # class. Count VISIBLE popups, not all matching DOM nodes.
            # Always choose '취소', never '확인' on an unknown dialog.
            for _ in range(4):
                dismissed = False
                for current_frame in page.frames:
                    try:
                        visible = current_frame.locator(
                            ".se-popup.se-popup-alert-confirm:visible"
                        )
                        if visible.count() == 0:
                            continue
                        popup = visible.first
                        cancel = popup.locator("button.se-popup-button-cancel:visible")
                        if cancel.count() != 1:
                            raise RuntimeError("NAVER_UNKNOWN_ALERT_REQUIRES_OWNER")
                        cancel.click(timeout=3500)
                        print("NAVER_EDITOR_ALERT_CANCELLED")
                        dismissed = True
                    except RuntimeError:
                        raise
                    except Exception:
                        raise RuntimeError("NAVER_ALERT_CANNOT_SAFELY_DISMISS")
                if not dismissed:
                    break
                page.wait_for_timeout(350)
            for current_frame in page.frames:
                if current_frame.locator(
                    ".se-popup.se-popup-alert-confirm:visible"
                ).count():
                    raise RuntimeError("NAVER_ALERT_STILL_VISIBLE_STOP")
            page.wait_for_timeout(350)
            frame, title, body_element = get_editor_frame(page)
            if not section_empty(frame, "title") or not section_empty(frame, "body"):
                raise RuntimeError("NAVER_EDITOR_NOT_EMPTY")
            title.click(timeout=8000)
            # SmartEditor can discard the first long insertText event while
            # activating a fresh title section. Prime it with one ASCII char,
            # allow its selection model to settle, then type the remaining
            # Unicode content like native keypresses (including emojis).
            page.keyboard.insert_text(article["title"][:1])
            page.wait_for_timeout(800)
            page.keyboard.type(article["title"][1:], delay=35)
            page.wait_for_timeout(900)
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
            if article.get("blocks"):
                render_readable_blocks(page, frame, body_element, article)
            else:
                # Compatibility with the original one-shot plain-text test.
                body_element.click(timeout=8000)
                enter_text_in_chunks(page, body)
                page.wait_for_timeout(700)
                actual_body = frame.locator(".se-section-text").inner_text()
                if (body[:32] not in actual_body
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
