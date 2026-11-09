"""One-shot Naver Blog editor test using an owner-supplied session.

No password handling, account-creation, stealth, CAPTCHA or 2FA bypass.
NEVER schedule this. The only external write is the explicit test post.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import parse_qs, urlsplit

BLOG_ID = "mw5114"
TEST_TITLE = "[자동화 테스트] 네이버 블로그 글쓰기 연결 확인"
TEST_BODY = (
    "네이버 블로그와 기존 GitHub 자동화의 글쓰기 연결을 확인하는 테스트 게시글입니다.\n\n"
    "현재는 시험 단계이며, 이 글에는 상품 홍보나 쿠팡 파트너스 링크가 포함되어 있지 않습니다.\n\n"
    "테스트 확인 후 삭제할 수 있습니다."
)
TITLE_SELECTORS = (
    ".se-section-documentTitle [contenteditable='true']",
    ".se-title-text [contenteditable='true']",
    ".se-section-documentTitle .se-text-paragraph",
)
BODY_SELECTORS = (
    ".se-section-text [contenteditable='true']",
    ".se-section-text .se-text-paragraph[contenteditable='true']",
    ".se-component-content [contenteditable='true']",
    ".se-section-text .se-text-paragraph",
)


def safely_parse_state(serialized: str) -> dict:
    """Accept only Naver login cookies and storage; never print their values."""
    if not serialized.strip():
        raise ValueError("AUTH_SESSION_MISSING")
    try:
        data = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ValueError("AUTH_SESSION_INVALID_JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise ValueError("AUTH_SESSION_INVALID_STRUCTURE")
    def owned(host):
        host = str(host or "").lower().lstrip(".")
        return host == "naver.com" or host.endswith(".naver.com")
    cookies = [v for v in data["cookies"] if isinstance(v, dict) and owned(v.get("domain"))]
    origins = [v for v in data.get("origins", []) if isinstance(v, dict) and
               urlsplit(str(v.get("origin", ""))).scheme == "https" and
               owned(urlsplit(str(v.get("origin", ""))).hostname)]
    if not cookies:
        raise ValueError("AUTH_SESSION_NO_NAVER_COOKIES")
    return {"cookies": cookies, "origins": origins}


def find_unique(frame, selectors):
    for css in selectors:
        locator = frame.locator(css)
        try:
            if locator.count() == 1 and locator.is_visible():
                return locator
        except Exception:
            pass
    return None


def get_editor_frame(page):
    for frame in page.frames:
        if "nid.naver.com" in (frame.url or "").lower():
            raise RuntimeError("AUTH_REQUIRED_OR_EXPIRED")
    for frame in page.frames:
        title = find_unique(frame, TITLE_SELECTORS)
        body = find_unique(frame, BODY_SELECTORS)
        if title is not None and body is not None:
            return frame, title, body
    # Inspect only sanitized frame topology and editor-element counts.
    # Never expose URLs with query strings, cookies, page text, or HTML.
    for i, frame in enumerate(page.frames[:8]):
        parsed = urlsplit(frame.url)
        domain = parsed.hostname or "unknown"
        known_path = parsed.path[:72]
        try:
            editables = frame.locator("[contenteditable=true]").count()
            inputs = frame.locator("input").count()
            title_class = frame.locator(".se-section-documentTitle").count()
            body_class = frame.locator(".se-section-text").count()
            login_field = frame.locator("input[type=password]").count()
        except Exception:
            editables = inputs = title_class = body_class = login_field = -1
        print(f"EDITOR_FRAME_{i}: DOMAIN={domain}; PATH={known_path}; "
              f"EDITABLES={editables}; INPUTS={inputs}; "
              f"TITLE_SECTIONS={title_class}; BODY_SECTIONS={body_class}; "
              f"PASSWORD_FIELDS={login_field}")
        try:
            for area in ("TITLE", "BODY"):
                sel = ".se-section-documentTitle" if area == "TITLE" else ".se-section-text"
                section = frame.locator(sel)
                if section.count() == 1:
                    meta = section.first.evaluate("""e => ({
                        tag: e.tagName,
                        className: (e.className || '').slice(0,90),
                        child: Array.from(e.querySelectorAll('p,div,[contenteditable]')).slice(0,8).map(x => ({
                            tag: x.tagName,
                            classes: (x.className || '').slice(0,90),
                            editable: x.getAttribute('contenteditable')
                        }))
                    })""")
                    print(f"SECTION_{i}_{area}={json.dumps(meta,ensure_ascii=True)}")
            edit_meta = frame.locator("[contenteditable]").evaluate_all("""els => els.slice(0,5).map(e => ({
                tag: e.tagName, classes: (e.className || '').slice(0,90),
                editable: e.getAttribute('contenteditable'),
                parent: (e.parentElement && e.parentElement.className || '').slice(0,90)
            }))""")
            print(f"EDITABLES_{i}={json.dumps(edit_meta,ensure_ascii=True)}")
        except Exception:
            print(f"SECTION_METADATA_UNAVAILABLE_{i}")
        for group, selectors in (("TITLE", TITLE_SELECTORS), ("BODY", BODY_SELECTORS)):
            for j, css in enumerate(selectors):
                try:
                    loc = frame.locator(css)
                    count = loc.count()
                    visible = loc.first.is_visible() if count else False
                    tag = loc.first.evaluate("(e) => e.tagName") if count else "-"
                    editable = loc.first.get_attribute("contenteditable") if count else "-"
                    print(f"SELECTOR_{i}_{group}{j}: N={count} V={visible} TAG={tag} CE={editable}")
                except Exception:
                    print(f"SELECTOR_{i}_{group}{j}: UNAVAILABLE")
    raise RuntimeError("EDITOR_CONTROLS_NOT_VERIFIED")


def publication_log_number(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.hostname not in ("blog.naver.com", "m.blog.naver.com"):
        return None
    number = (parse_qs(parsed.query).get("logNo") or [None])[0]
    if not number:
        match = re.search(r"/mw5114/(\d+)(?:/|$)", parsed.path)
        number = match.group(1) if match else None
    return number if number and re.fullmatch(r"\d{8,18}", number) else None


def publish_exactly_one(frame, page):
    """Allow only two deliberate '발행' clicks, then verify an actual post URL."""
    buttons = frame.get_by_role("button", name="발행", exact=True)
    if buttons.count() != 1 or not buttons.first.is_visible():
        raise RuntimeError("PUBLISH_BUTTON_NOT_UNIQUE")
    buttons.first.click(timeout=7000)
    page.wait_for_timeout(1300)
    popup_buttons = frame.get_by_role("button", name="발행", exact=True)
    shown = [popup_buttons.nth(i) for i in range(min(popup_buttons.count(), 8))
             if popup_buttons.nth(i).is_visible()]
    if len(shown) < 2:
        raise RuntimeError("PUBLISH_CONFIRMATION_NOT_VERIFIED")
    # Prefer explicit publish dialog buttons; never guess when ambiguous.
    confirm_candidates = [
        element for element in shown
        if element.locator("xpath=ancestor::*[contains(@class,'publish') or @role='dialog']").count() > 0
    ]
    if len(confirm_candidates) != 1:
        raise RuntimeError("PUBLISH_CONFIRMATION_AMBIGUOUS")
    confirm_candidates[0].click(timeout=7000)
    page.wait_for_timeout(2500)

    # Check the navigation target; no retries after any submission click.
    candidates = [page.url] + [f.url for f in page.frames]
    for target in candidates:
        log_no = publication_log_number(target)
        if log_no:
            return log_no
    raise RuntimeError("SUBMISSION_UNCERTAIN_DO_NOT_RETRY")


def run(phase: str) -> int:
    try:
        state = safely_parse_state(os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""))
    except ValueError as exc:
        print(str(exc))
        return 3

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=state, locale="ko-KR")
            page = context.new_page()
            try:
                response = page.goto(
                    f"https://blog.naver.com/PostWriteForm.naver?blogId={BLOG_ID}",
                    wait_until="domcontentloaded",
                    timeout=35000,
                )
                page.wait_for_timeout(3000)
                if response is None or response.status >= 400:
                    raise RuntimeError("EDITOR_HTTP_ACCESS_FAILED")
                for frame in page.frames:
                    try:
                        popup = frame.locator("button.se-popup-button-cancel")
                        if popup.count() == 1 and popup.is_visible():
                            popup.click(timeout=4000)
                    except Exception:
                        pass
                page.wait_for_timeout(700)
                frame, title, body = get_editor_frame(page)
                print("AUTH_SESSION_OK; EDITOR_CONTROLS_FOUND")
                if phase == "probe":
                    print("EDITOR_READ_ONLY_SUCCESS")
                    return 0

                if title.inner_text().strip() or body.inner_text().strip():
                    raise RuntimeError("EDITOR_ALREADY_CONTAINS_TEXT_STOP")
                # SmartEditor ONE uses focus-managed contenteditable, not an
                # editable <p> itself. Click the section to focus its editor,
                # then type through keyboard as a human would.
                title.click(timeout=10000)
                page.keyboard.insert_text(TEST_TITLE)
                page.wait_for_timeout(650)
                if TEST_TITLE not in title.inner_text():
                    raise RuntimeError("TITLE_INPUT_NOT_VERIFIED")
                body.click(timeout=10000)
                page.keyboard.insert_text(TEST_BODY)
                page.wait_for_timeout(800)
                if "GitHub 자동화" not in body.inner_text():
                    raise RuntimeError("BODY_INPUT_NOT_VERIFIED")
                print("TEST_CONTENT_FILLED")
                if phase == "draft":
                    print("DRAFT_ONLY_NO_PUBLISH_CLICK")
                    return 0

                log_no = publish_exactly_one(frame, page)
                print(f"POST_URL=https://blog.naver.com/{BLOG_ID}/{log_no}")
                print("PUBLISH_NAVIGATION_CONFIRMED")
                return 0
            except Exception as exc:
                # Never include browser/Playwright exception text: might expose URLs or private data.
                code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                print(f"NAVER_BLOG_STOP={code}")
                return 4
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("probe", "draft", "publish"), required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.phase))
