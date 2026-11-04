"""One Daangn publish attempt using the photo-first flow already verified live.

Caller is responsible for durable pre-click claim. This module never retries a
submit and does not mutate the queue. It returns verification evidence only.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from gh_only.daangn_editor_probe import (
    parse_daangn_storage_state, DISCLOSURE, verify_link_in_one_paragraph,
)

TARGET = "https://cafe.daangn.com/i-gagyeog-mos-c/posts/new"
PREFIX = "/i-gagyeog-mos-c/posts/"
BOARD = "🔥 오늘의 핫딜"


def deal_expired_before_submit(article: dict, reference=None) -> bool:
    """Keep 60-minute gate except explicitly user-replayed 94986-94990."""
    from datetime import datetime, timezone
    from gh_only.official_link_receipts import parse_timestamp
    raw = article.get("posted_at_utc")
    if not raw:
        return False  # existing caller enforces source-time provenance
    posted = parse_timestamp(raw)
    if posted is None:
        return True
    elapsed = ((reference or datetime.now(timezone.utc)) - posted).total_seconds()
    if elapsed < 3600:
        return False
    return not (article.get("manual_replay_approved") is True
                and article.get("source_id") in (94986, 94987, 94988, 94989, 94990))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def fit_title_to_limit(title: str, max_length: int) -> str:
    """Preserve the [NN%할인] suffix when Daangn limits post title length."""
    if not isinstance(max_length, int) or max_length < 20 or max_length > 200:
        raise ValueError("UNSAFE_DAANGN_TITLE_LIMIT")
    if len(title) <= max_length:
        return title
    discount = re.search(r" (\[\d+%할인\])$", title)
    if discount:
        suffix = discount.group(0)
        keep = max_length - len(suffix)
        if keep < 15:
            raise ValueError("DAANGN_TITLE_LIMIT_TOO_SHORT_FOR_DISCOUNT")
        return title[:keep].rstrip() + suffix
    return title[:max_length].rstrip()


def choose_editor_submit_index(candidates: list[dict]) -> int | None:
    """Choose only one visible exact-label button positioned below editor top."""
    eligible = [row["index"] for row in candidates
                if row.get("visible") and row.get("below_editor")]
    return eligible[0] if len(eligible) == 1 else None


def leading_image_ui_prefix(observed: str, body: str) -> str | None:
    """Allow short image-control text BEFORE otherwise exact post content.

    The rich editor can prepend UI labels to an image node. Reject any
    substitution, insertion within body, changed footer, or excessive prefix.
    """
    actual, expected = norm(observed), norm(body)
    if actual == expected:
        return ""
    if not actual.endswith(expected):
        return None
    prefix = actual[:-len(expected)]
    return prefix if 0 < len(prefix) <= 60 else None


def validate_editor_copy(editor, body: str, url: str, image) -> bool:
    observed = editor.inner_text().strip()
    prefix = leading_image_ui_prefix(observed, body)
    # The exact post text follows the uploaded photo. A small leading label
    # within image UI is permitted, but the actual article stays byte-for-byte
    # intact after whitespace normalization and keeps its exact disclosure.
    exact = prefix is not None
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
        and verify_link_in_one_paragraph(editor, url)
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
        "photo_upload_completed": False,
        "failure_stage": "INITIALIZING",
    }
    try:
        result["failure_stage"] = "LOAD_DAANGN_SESSION"
        state = parse_daangn_storage_state(
            state_secret if state_secret is not None else os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON", "")
        )
        with sync_playwright() as p:
            result["failure_stage"] = "LAUNCH_BROWSER"
            browser = p.chromium.launch(headless=True)
            try:
                result["failure_stage"] = "OPEN_DAANGN_PAGE"
                ctx = browser.new_context(
                    storage_state=state, locale="ko-KR",
                    viewport={"width": 1366, "height": 950},
                )
                page = ctx.new_page()
                resp = page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1800)
                result["http_status"] = resp.status if resp else None
                result["page_path"] = urlsplit(page.url).path
                if (not resp or resp.status != 200
                    or urlsplit(page.url).hostname != "cafe.daangn.com"
                    or urlsplit(page.url).path != PREFIX + "new"):
                    raise RuntimeError("EXPECTED_EDITOR_NOT_REACHED")
                result["failure_stage"] = "SELECT_BOARD"
                board = page.locator("select")
                if board.count() != 1:
                    raise RuntimeError("BOARD_NOT_UNIQUE")
                board.select_option(label=BOARD)
                if board.locator("option:checked").inner_text().strip() != BOARD:
                    raise RuntimeError("WRONG_BOARD")
                result["failure_stage"] = "FIND_EDITOR"
                editor = page.locator("div.ProseMirror[contenteditable=true]")
                if editor.count() != 1:
                    raise RuntimeError("EDITOR_NOT_UNIQUE")
                result["failure_stage"] = "VERIFY_IMAGE_FILE"
                photo = article.get("image")
                if photo is None or not photo.is_file():
                    raise RuntimeError("PRODUCT_PHOTO_NOT_AVAILABLE")
                result["failure_stage"] = "UPLOAD_IMAGE"
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
                # The editor displays a local preview before the image is
                # uploaded. Verified with a no-submit probe: submit became
                # enabled at ~2 seconds while the '준비중' upload label remained;
                # the upload actually finished at ~7 seconds. A click before
                # then creates a post with no product photo.
                result["failure_stage"] = "WAIT_IMAGE_UPLOAD_FINISH"
                page.wait_for_function("""() => {
                  const e=document.querySelector('div.ProseMirror[contenteditable=true]');
                  const img=e && e.querySelector('img');
                  return !!img && img.complete && img.naturalWidth>0 &&
                      !(e.innerText||'').includes('준비중');
                }""", timeout=35000)
                result["photo_upload_completed"] = True
                result["photo_uploaded_first"] = True
                # The successful live test put photo FIRST then appended text.
                result["failure_stage"] = "WRITE_EDITOR_BODY"
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
                result["failure_stage"] = "WRITE_TITLE"
                title_input = page.locator('input[placeholder="제목을 입력해주세요."]')
                if title_input.count() != 1:
                    raise RuntimeError("TITLE_NOT_UNIQUE")
                original_title = article["title"]
                html_max_length = title_input.get_attribute("maxlength")
                max_length = (
                    int(html_max_length) if html_max_length
                    and html_max_length.isdecimal() else None
                )
                post_title = (
                    fit_title_to_limit(original_title, max_length)
                    if max_length and len(original_title) > max_length
                    else original_title
                )
                title_input.fill(post_title)
                page.wait_for_timeout(1200)
                observed_title = title_input.input_value()
                result["title_checks"] = {
                    "original_length": len(original_title),
                    "html_max_length": max_length,
                    "attempted_length": len(post_title),
                    "observed_length": len(observed_title),
                    "matched": observed_title == post_title,
                }
                if observed_title != post_title:
                    # When React or a maxlength rule silently clips a title,
                    # derive the real limit from the observed prefix and retry
                    # once, preserving the discount suffix.
                    if (len(observed_title) >= 20 and
                        post_title.startswith(observed_title) and
                        len(observed_title) < len(post_title)):
                        post_title = fit_title_to_limit(
                            original_title, len(observed_title)
                        )
                    title_input.fill(post_title)
                    page.wait_for_timeout(700)
                    result["title_checks"]["retried_length"] = len(post_title)
                    result["title_checks"]["matched_after_retry"] = (
                        title_input.input_value() == post_title
                    )
                if title_input.input_value() != post_title:
                    raise RuntimeError("TITLE_NOT_VERIFIED")
                result["failure_stage"] = "VERIFY_EDITOR_BODY"
                # Store booleans/counts only, never the editable page contents.
                observed = editor.inner_text().strip()
                result["editor_checks"] = {
                    "normalized_body_equal": norm(observed) == norm(article["body"]),
                    "body_is_exact_suffix": norm(observed).endswith(norm(article["body"])),
                    "leading_image_ui_prefix_length": len(
                        leading_image_ui_prefix(observed, article["body"]) or ""
                    ),
                    "leading_image_ui_prefix_present": bool(
                        leading_image_ui_prefix(observed, article["body"])
                    ),
                    "candidate_url_count": observed.count(article["candidate_url"]),
                    "link_row_contiguous": verify_link_in_one_paragraph(
                        editor, article["candidate_url"]
                    ),
                    "disclosure_last": observed.rstrip().endswith(DISCLOSURE),
                    "image_count": image.count(),
                    "observed_length": len(observed),
                    "expected_length": len(article["body"]),
                }
                if not validate_editor_copy(
                    editor, article["body"], article["candidate_url"], image
                ):
                    raise RuntimeError("EDITOR_CONTENT_OR_FULL_URL_NOT_VERIFIED")
                if board.locator("option:checked").inner_text().strip() != BOARD:
                    raise RuntimeError("BOARD_RESET")
                result["failure_stage"] = "LOCATE_SUBMIT_BUTTON"
                # Do not depend solely on the brittle Tailwind w-full class.
                # Prefer the accessible label, with the previously working
                # CSS selector as a compatibility fallback.
                role_submit = page.get_by_role("button", name="글쓰기", exact=True)
                legacy_submit = page.locator("button.w-full").filter(
                    has_text=re.compile(r"^\\s*글쓰기\\s*$")
                )
                result["submit_checks"] = {
                    "role_count": role_submit.count(),
                    "legacy_count": legacy_submit.count(),
                    "upload_pending_label": "준비중" in editor.inner_text(),
                }
                editor_rect = editor.bounding_box()
                candidates = []
                for idx in range(role_submit.count()):
                    candidate = role_submit.nth(idx)
                    rect = candidate.bounding_box() if candidate.is_visible() else None
                    below_editor = bool(
                        rect and editor_rect and rect["y"] > editor_rect["y"]
                    )
                    candidates.append({
                        "index": idx,
                        "visible": bool(rect),
                        "below_editor": below_editor,
                        "enabled": candidate.is_enabled(),
                        "y": round(rect["y"]) if rect else None,
                    })
                result["submit_checks"]["candidates"] = candidates
                anchored_index = choose_editor_submit_index(candidates)
                if anchored_index is not None:
                    submit = role_submit.nth(anchored_index)
                    result["submit_checks"]["strategy"] = "single_button_below_editor"
                elif role_submit.count() == 1 and role_submit.is_visible():
                    # One accessible button, but do not click a header/nav action
                    # when the editor location is available.
                    if editor_rect and anchored_index is None:
                        raise RuntimeError("SOLE_SUBMIT_BUTTON_ABOVE_EDITOR")
                    submit = role_submit
                    result["submit_checks"]["strategy"] = "sole_accessible_role"
                elif legacy_submit.count() == 1:
                    legacy_rect = legacy_submit.bounding_box()
                    if (editor_rect and legacy_rect
                        and legacy_rect["y"] <= editor_rect["y"]):
                        raise RuntimeError("LEGACY_BUTTON_ABOVE_EDITOR")
                    submit = legacy_submit
                    result["submit_checks"]["strategy"] = "legacy_css"
                else:
                    raise RuntimeError("NO_UNIQUE_EDITOR_ANCHORED_SUBMIT_BUTTON")
                result["failure_stage"] = "WAIT_SUBMIT_ENABLED"
                submit.wait_for(state="visible", timeout=10000)
                # Image has appeared locally but may still be uploading.
                # Let the site enable the button instead of aborting instantly.
                try:
                    from playwright.sync_api import expect
                    expect(submit).to_be_enabled(timeout=18000)
                finally:
                    result["submit_checks"]["enabled_after_wait"] = submit.is_enabled()
                    result["submit_checks"]["upload_pending_after_wait"] = (
                        "준비중" in editor.inner_text()
                    )
                if not submit.is_enabled():
                    raise RuntimeError("SUBMIT_STILL_DISABLED_AFTER_UPLOAD_WAIT")
                if not result.get("photo_upload_completed") or "준비중" in editor.inner_text():
                    raise RuntimeError("PHOTO_UPLOAD_NOT_CONFIRMED_DO_NOT_SUBMIT")
                if image.count() != 1 or not image.first.evaluate(
                    "(e)=>e.complete&&e.naturalWidth>0"
                ):
                    raise RuntimeError("PHOTO_MISSING_BEFORE_SUBMIT")
                # A React/editor rerender may split a URL after initial typing.
                # Refuse a submit unless the full link remains one paragraph.
                if not verify_link_in_one_paragraph(editor, article["candidate_url"]):
                    raise RuntimeError("PRODUCT_LINK_PARAGRAPH_SPLIT_DO_NOT_SUBMIT")
                # The deal may have aged past 60 minutes while the image
                # uploaded. Do not submit such a post, even if already claimed.
                if deal_expired_before_submit(article):
                    raise RuntimeError("DEAL_EXPIRED_BEFORE_SUBMIT")
                # No retry under ANY condition, including timeout or navigation error.
                result["failure_stage"] = "SUBMIT_CLICKED_OUTCOME_UNCERTAIN"
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
                result["failure_stage"] = "VERIFY_PUBLISHED_POST"
                content = page.locator("body").inner_text(timeout=5000)
                if article.get("muse_rewrite_pending") is True:
                    hrefs = page.locator('a[href*="www.coupang.com/vp/products/"]').evaluate_all(
                        "(nodes)=>nodes.map(n=>n.getAttribute('href'))"
                    )
                    same_link = len(hrefs) == 1 and hrefs[0] == article["candidate_url"]
                elif article.get("official_link_receipt") is not None:
                    hrefs = page.locator('a[href*="link.coupang.com/a/"]').evaluate_all(
                        "(nodes)=>nodes.map(n=>n.getAttribute('href'))"
                    )
                    same_link = len(hrefs) == 1 and hrefs[0] == article["candidate_url"]
                else:
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
                title_ok = post_title in content
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
        else:
            # Avoid writing raw Playwright exception text: it may include
            # URLs, page labels or user/account information.
            result["error_reason"] = "PLAYWRIGHT_" + type(exc).__name__.upper()
    return result
