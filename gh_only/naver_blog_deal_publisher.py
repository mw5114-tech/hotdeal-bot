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
        # A programmatic Range alone does not activate SmartEditor's
        # format command. Click the actual paragraph first, then select its
        # current line using native keys with an editor-owned caret.
        paragraph.first.click(timeout=5000)
        page.keyboard.press("Home")
        page.keyboard.press("Shift+End")
        # SmartEditor's real native bold buttons. Chrome/Linux Ctrl+B does
        # not consistently apply styling in this content section.
        bold = frame.locator(
            "button.se-bold-toolbar-button.se-property-toolbar-toggle-button:visible"
        )
        if bold.count() != 1:
            bold = frame.locator(
                "button.se-bold-toolbar-button.se-contents-toolbar-toggle-button:visible"
            )
        if bold.count() != 1:
            print("NAVER_BOLD_TOOLBAR_UNAVAILABLE " + str(item.get("kind")))
            continue
        bold.first.click(timeout=5000)
        page.wait_for_timeout(260)
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


def _affiliate_anchors(frame, url: str, label: str) -> dict:
    """Validate the exact persisted hyperlink model used by Naver SmartEditor.

    Editing mode stores the destination in span.se-link[data-href] instead
    of a[href]. Reject any partial hyperlink range, duplicate label, visible
    raw URL or wrong affiliate destination before publication.
    """
    matches = frame.locator(
        ".se-component a[href], .se-section-text .se-link[data-href]"
    ).evaluate_all(
        """(els, args) => els.filter(e => {
            const href = e.matches('a[href]') ? e.getAttribute('href') :
                         e.getAttribute('data-href');
            const paragraph = e.closest('.se-text-paragraph');
            return href === args.url
                && (e.innerText || '').trim() === args.label
                && paragraph && (paragraph.innerText || '').trim() === args.label;
        }).map(e => ({
            component: (e.closest('.se-component')?.className || '').slice(0,80),
            native_model: e.matches('.se-link[data-href]'),
            label_match: (e.innerText || '').trim() === args.label
        }))""", {"url": url, "label": label}
    )
    return {"count": len(matches),
            "components": [m["component"] for m in matches],
            "native_models": [m["native_model"] for m in matches],
            "label_matches": [m["label_match"] for m in matches]}


def _apply_native_affiliate_link(page, frame, url: str, label: str) -> bool:
    """Apply a real href to selected URL via SmartEditor's native link form.

    Never fake an HTML anchor in the DOM: use the editor's own persisted link
    model, then verify the actual href before allowing publication.
    """
    paragraph = frame.locator(".se-section-text .se-text-paragraph").filter(
        has_text=label
    )
    if paragraph.count() != 1 or paragraph.first.inner_text().strip() != label:
        raise RuntimeError("NAVER_LINK_LABEL_PARAGRAPH_AMBIGUOUS")
    # Establish an actual selection before clicking Naver's link UI.
    # Record selection from both page/iframe for diagnosis, without URLs.
    paragraph.first.select_text(timeout=5000)
    frame_selected = frame.evaluate(
        "() => (window.getSelection()?.toString() || '').trim()"
    )
    page_selected = page.evaluate(
        "() => (window.getSelection()?.toString() || '').trim()"
    )
    print("NAVER_CTA_SELECTION_CHECK " + str({
        "frame_selected_chars": len(frame_selected),
        "page_selected_chars": len(page_selected),
        "expected_chars": len(label),
        "frame_exact": frame_selected == label,
        "page_exact": page_selected == label,
    }))
    if frame_selected != label and page_selected != label:
        structure = paragraph.first.evaluate(
            """e=>({html:e.outerHTML.slice(0,1000),
                userSelect:getComputedStyle(e).userSelect,
                contentEditable:e.getAttribute('contenteditable')})"""
        )
        print("NAVER_CTA_SELECTOR_STRUCTURE " + str(structure))
        raise RuntimeError("NAVER_CTA_FULL_SELECTION_NOT_ESTABLISHED")
    toolbar = frame.locator(
        "button.se-link-toolbar-button.se-property-toolbar-custom-layer-button:visible"
    )
    if toolbar.count() != 1:
        raise RuntimeError("NAVER_NATIVE_LINK_BUTTON_UNAVAILABLE")
    toolbar.click(timeout=4500)
    field = frame.locator("input.se-custom-layer-link-input:visible")
    apply = frame.locator("button.se-custom-layer-link-apply-button:visible")
    if field.count() != 1 or apply.count() != 1:
        raise RuntimeError("NAVER_NATIVE_LINK_DIALOG_UNVERIFIED")
    field.fill(url, timeout=6000)
    if field.input_value() != url:
        raise RuntimeError("NAVER_NATIVE_LINK_URL_INPUT_MISMATCH")
    apply.click(timeout=6000)
    page.wait_for_timeout(950)
    result = _affiliate_anchors(frame, url, label)
    print("NAVER_NATIVE_LINK_APPLY_RESULT " + str(result))
    if result["count"] != 1:
        spans = frame.locator(".se-section-text .se-link, .se-section-text a[href]").evaluate_all(
            """(els, args) => els.slice(-12).map(e => {
                const text=(e.innerText||'').trim();
                const par=(e.closest('.se-text-paragraph')?.innerText||'').trim();
                const href=e.getAttribute('data-href')||e.getAttribute('href')||'';
                return {
                    node:e.tagName, char_count:text.length, paragraph_chars:par.length,
                    text_equals_label:text===args.label,
                    paragraph_equals_label:par===args.label,
                    text_is_prefix:args.label.startsWith(text),
                    text_is_suffix:args.label.endsWith(text),
                    href_exact:href===args.url,
                    href_char_count:href.length,
                    node_classes:(e.className||'').slice(0,90),
                    label_preview:text.slice(0,50)
                };
            })""", {"url":url,"label":label}
        )
        print("NAVER_SHORT_CTA_SPAN_DIAGNOSTIC " + str(spans))
        print("NAVER_SHORT_CTA_PARAGRAPH " + str([
            x[:90] for x in frame.locator(
                ".se-section-text .se-text-paragraph"
            ).all_inner_texts() if "상품정보 더보기" in x
        ]))
    return result["count"] == 1


def _insert_clean_standalone_url(page, frame, url: str) -> bool:
    """Use SmartEditor's insertion-style link UI in its own empty paragraph.

    Unlike selected-text hyperlink attempts, this cannot leave a plain-text
    URL prefix/suffix because the visible URL was never typed in the editor.
    The entire URL must be one native se-link[data-href] span, with exactly
    the same URL as its parent paragraph's complete visible text.
    """
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")
    page.wait_for_timeout(550)
    last_para = frame.locator(".se-section-text .se-text-paragraph").last
    if not last_para.count() or last_para.inner_text().strip():
        raise RuntimeError("NAVER_STANDALONE_LINK_EMPTY_PARAGRAPH_MISSING")
    toolbar = frame.locator(
        "button.se-link-toolbar-button.se-property-toolbar-custom-layer-button:visible"
    )
    if toolbar.count() != 1:
        raise RuntimeError("NAVER_STANDALONE_LINK_TOOLBAR_UNAVAILABLE")
    toolbar.click(timeout=5000)
    field = frame.locator("input.se-custom-layer-link-input:visible")
    apply = frame.locator("button.se-custom-layer-link-apply-button:visible")
    if field.count() != 1 or apply.count() != 1:
        raise RuntimeError("NAVER_STANDALONE_LINK_FORM_INVALID")
    field.fill(url, timeout=7000)
    if field.input_value() != url:
        raise RuntimeError("NAVER_STANDALONE_LINK_FORM_URL_MISMATCH")
    apply.click(timeout=7000)
    page.wait_for_timeout(1000)
    result = _affiliate_anchors(frame, url, url)
    visible_paragraphs = frame.locator(
        ".se-section-text .se-text-paragraph"
    ).all_inner_texts()
    urls = [p.strip() for p in visible_paragraphs if "link.coupang.com" in p]
    status = {
        "native_link_count": result["count"],
        "url_paragraphs": len(urls),
        "sole_paragraph_exact": len(urls) == 1 and urls[0] == url,
        "url_text_occurrences": "\n".join(visible_paragraphs).count(url),
    }
    print("NAVER_STANDALONE_LINK_CHECK " + str(status))
    return bool(
        status["native_link_count"] == 1
        and status["sole_paragraph_exact"]
        and status["url_text_occurrences"] == 1
    )


def _diagnose_link_toolbar(page, frame, url: str):
    candidates = frame.locator("button").evaluate_all(
        """els => els.filter(b => /link|링크|연결|url/i.test(
            [b.className,b.title,b.getAttribute('aria-label'),b.innerText].join(' ')
        )).slice(0,15).map(b => ({
            cls: (b.className || '').slice(0,140),
            label: (b.innerText || '').slice(0,50),
            visible: b.getClientRects().length > 0
        }))"""
    )
    print("NAVER_LINK_TOOLBAR_CANDIDATES " + str(candidates))
    paragraph = frame.locator(".se-section-text .se-text-paragraph").filter(has_text=url)
    if paragraph.count() != 1:
        print("NAVER_LINK_PARAGRAPH_NON_UNIQUE=" + str(paragraph.count()))
        return
    paragraph.first.click(timeout=5000)
    page.keyboard.press("Home")
    page.keyboard.press("Shift+End")
    toolbar = frame.locator(
        "button.se-link-toolbar-button.se-property-toolbar-custom-layer-button:visible"
    )
    print("NAVER_LINK_TOOLBAR_COUNT=" + str(toolbar.count()))
    if toolbar.count() != 1:
        return
    toolbar.click(timeout=4500)
    page.wait_for_timeout(550)
    inputs = frame.locator("input, textarea").evaluate_all(
        """els => els.filter(e => e.getClientRects().length > 0).slice(0,20).map(e => ({
            tag: e.tagName, type:e.getAttribute('type'),
            placeholder:(e.getAttribute('placeholder')||'').slice(0,75),
            cls:(e.className||'').slice(0,110)
        }))"""
    )
    controls = frame.locator("button").evaluate_all(
        """els => els.filter(b => b.getClientRects().length > 0 &&
            /확인|적용|저장|링크|취소|완료|연결/i.test(b.innerText || '')
        ).slice(0,18).map(b => ({
            label:(b.innerText || '').slice(0,45),
            cls:(b.className||'').slice(0,120)
        }))"""
    )
    layers = frame.locator("[class*='se-link']").evaluate_all(
        """els => els.filter(e => e.getClientRects().length > 0).slice(-16).map(e => ({
            tag:e.tagName, cls:(e.className||'').slice(0,110),
            text:(e.innerText||'').slice(0,80)
        }))"""
    )
    print("NAVER_LINK_DIALOG_INPUTS " + str(inputs))
    print("NAVER_LINK_DIALOG_BUTTONS " + str(controls))
    print("NAVER_LINK_DIALOG_LAYERS " + str(layers))


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

    page.wait_for_timeout(650)
    # Naver's property link form INSERTS a new URL rather than wrapping
    # selected user-facing text. Reserve its own empty paragraph so it never
    # splits or duplicates the visible URL alongside the short CTA heading.
    cta_label = blocks[-1]["text"]
    if blocks[-1].get("kind") != "affiliate_link":
        raise RuntimeError("NAVER_AFFILIATE_CTA_NOT_LAST")
    if not _insert_clean_standalone_url(page, frame, article["link"]):
        raise RuntimeError("NAVER_STANDALONE_LINK_NOT_VERIFIED")
    bold_applied = _bold_existing_labels(page, frame, blocks)
    page.wait_for_timeout(550)
    section = "\n".join(frame.locator(".se-section-text").all_inner_texts())
    conditions = {
        "affiliate_anchor": _affiliate_anchors(
            frame, article["link"], article["link"]
        )["count"] == 1,
        "disclosure": blocks[0]["text"] in section,
        "sale": "오늘 특가" in section,
        "cta_label_once": section.count(cta_label) == 1,
        "full_link_once": section.count(article["link"]) == 1,
        "no_duplicate_link_fragments": len([
            p for p in frame.locator(
                ".se-section-text .se-text-paragraph"
            ).all_inner_texts() if "link.coupang.com" in p
        ]) == 1,
        "closing_note": any(
            x.get("kind") == "note" and x["text"] in section
            for x in blocks
        ),
        "paragraphs": frame.locator(".se-text-paragraph").count(),
        "bold_applied": bold_applied,
        "computed_bold_paragraphs": frame.locator(
            ".se-section-text .se-text-paragraph"
        ).evaluate_all(
            """els => els.filter(e => Array.from(
                 e.querySelectorAll('span,strong,b')
                ).some(x => {
                    const w = getComputedStyle(x).fontWeight;
                    return w === 'bold' || parseInt(w,10) >= 600;
                })).length"""
        ),
        "bold_segments": frame.locator(
            ".se-section-text strong, .se-section-text b, "
            ".se-section-text span[style*='font-weight: bold'], "
            ".se-section-text .se-text-format-bold"
        ).count(),
        "photo_between": _photo_between_disclosure_and_price(frame, blocks[0]["text"]),
    }
    print("NAVER_FORMAT_CHECK " + str(conditions))
    if conditions["bold_applied"] >= 3 and conditions["bold_segments"] < 3:
        try:
            buttons = frame.locator("button").evaluate_all(
                """els => els.filter(b => /bold|굵게|strong/i.test(
                    [b.className,b.title,b.getAttribute('aria-label'),b.innerText].join(' ')
                )).slice(0,12).map(b => ({
                    cls: (b.className || '').slice(0,110),
                    title: (b.title || '').slice(0,50),
                    aria: (b.getAttribute('aria-label') || '').slice(0,50),
                    text: (b.innerText || '').slice(0,25),
                    visible: b.getClientRects().length > 0
                }))"""
            )
            print("NAVER_BOLD_TOOLBAR_CANDIDATES " + str(buttons))
        except Exception:
            print("NAVER_BOLD_TOOLBAR_DIAGNOSTIC_UNAVAILABLE")
        try:
            metrics = frame.locator(".se-section-text .se-text-paragraph").evaluate_all(
                """els => els.filter(e => /오늘 특가|평소 판매가|참고 할인율|오늘의 쿠팡|상품정보/.test(e.innerText || '')).slice(0,12).map(e => ({
                    text: (e.innerText || '').slice(0,55),
                    rootWeight: getComputedStyle(e).fontWeight,
                    children: Array.from(e.querySelectorAll('span,strong,b')).slice(0,6).map(s => ({
                        tag: s.tagName,
                        cls: (s.className || '').slice(0,90),
                        weight: getComputedStyle(s).fontWeight
                    }))
                }))"""
            )
            print("NAVER_COMPUTED_BOLD_DIAGNOSTIC " + str(metrics))
        except Exception:
            print("NAVER_COMPUTED_BOLD_DIAGNOSTIC_UNAVAILABLE")
    if not conditions["sale"] or not conditions["photo_between"]:
        # The price/label copy is intentionally public; do not log full URLs.
        snippets = []
        for i, para in enumerate(frame.locator(".se-text-paragraph").all_inner_texts()[:28]):
            stripped = para.replace(article["link"], "[AFFILIATE_LINK]")
            snippets.append((i, stripped[:100]))
        print("NAVER_PARAGRAPH_DIAGNOSTIC " + str(snippets))
    _editor_layout(frame, "FINAL")
    if not all((conditions["disclosure"], conditions["sale"],
                conditions["cta_label_once"], conditions["full_link_once"],
                conditions["no_duplicate_link_fragments"],
                conditions["closing_note"],
                conditions["photo_between"])):
        raise RuntimeError("NAVER_FORMAT_STRUCTURE_NOT_VERIFIED")
    if not conditions["affiliate_anchor"]:
        raise RuntimeError("NAVER_AFFILIATE_CTA_NOT_FULLY_CLICKABLE")
    if conditions["paragraphs"] < 7:
        raise RuntimeError("NAVER_FORMAT_PARAGRAPHS_MISSING")
    if conditions["bold_applied"] < 3 or conditions["computed_bold_paragraphs"] < 3:
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
            or "https://link.coupang.com/" in body
            or body.count("📦 쿠팡에서 상품정보 더보기") != 1
            or not isinstance(article.get("link"), str)
            or not article["link"].startswith("https://link.coupang.com/re/AFFSDP?")
            or not isinstance(article.get("blocks"), list)):
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
