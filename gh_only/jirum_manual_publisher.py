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

# The restored workflow overrides the archived cafe; no old posts are moved.
RESTORED_CAFE = "https://cafe.daangn.com/kupang-teuggaye"

def target_for_cafe(base_url: str) -> tuple[str, str]:
    """Use a fixed, known Daangn-owned cafe; never allow arbitrary destinations."""
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or parsed.hostname != "cafe.daangn.com"
            or parsed.port is not None or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") not in (
                "/i-gagyeog-mos-c", "/kupang-teuggaye"
            )):
        raise RuntimeError("DAANGN_CAFE_TARGET_NOT_APPROVED")
    prefix = parsed.path.rstrip("/") + "/posts/"
    return "https://cafe.daangn.com" + prefix + "new", prefix


def restored_board_label() -> str:
    """Exact board label observed on user's new cafe in a no-post probe."""
    import os
    if os.getenv("DAANGN_CAFE_RESTORED") == "true":
        label = os.getenv("DAANGN_CAFE_BOARD_LABEL")
        if label != "🔥 오늘의핫딜":
            raise RuntimeError("DAANGN_CAFE_BOARD_LABEL_UNVERIFIED")
        return label
    return BOARD


def restored_live_gate() -> bool:
    """Require explicit channel approval and live enabled, never implicit."""
    import os
    return all((
        os.getenv("DAANGN_CAFE_RESTORED") == "true",
        os.getenv("DAANGN_CAFE_PUBLISH_ENABLED") == "true",
        os.getenv("DAANGN_CAFE_PARTNERS_PERMISSION_CONFIRMED") == "true",
        os.getenv("DAANGN_PUBLISH_PAUSED") == "false",
        os.getenv("DAANGN_OFFICIAL_API_LINK_ENABLED") == "true",
        os.getenv("COUPANG_PARTNER_SUBID") == "cafe",
    ))


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


def validate_editor_copy(editor, body: str, url: str, image,
                         *, chart_attached: bool = False) -> bool:
    observed = editor.inner_text().strip()
    prefix = leading_image_ui_prefix(observed, body)
    # With two image nodes, ProseMirror can introduce image UI labels between
    # title and reference. Compare actual text paragraphs, never ignore a
    # missing or changed price/link/disclosure line.
    if chart_attached:
        # The editor inserts invisible spacer paragraphs around images.
        # Preserve the original strict text-suffix check: an image UI prefix
        # of at most 60 characters is allowed, but the COMPLETE post text
        # must remain unchanged and in sequence. Also verify the actual
        # image DOM position separately; never accept a chart at the end.
        exact = prefix is not None
        disclosure_last = editor.evaluate(r"""e =>
          [...e.children].filter(x=>x.matches('p'))
          .map(x=>x.innerText.trim()).filter(Boolean).at(-1)""") == DISCLOSURE
    else:
        exact = prefix is not None
        disclosure_last = observed.rstrip().endswith(DISCLOSURE)
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
        and disclosure_last
        and url_one_node
        and verify_link_in_one_paragraph(editor, url)
        and (not chart_attached or _chart_between_product_and_reference(editor))
        and image.count() == (2 if chart_attached else 1)
        and image.evaluate_all(
            "(nodes)=>nodes.every(e=>e.complete&&e.naturalWidth>0)"
        )
    )


def _chart_between_product_and_reference(editor) -> bool:
    """Strict DOM order: first body line -> chart image -> price reference."""
    return bool(editor.evaluate(r"""root => {
      const imgs = [...root.querySelectorAll('img')];
      const ps = [...root.querySelectorAll('p')];
      const product = ps.find(p => /^\[(?:\d+%할인|1% 미만 저렴|할인율 확인 중)\]/.test(
          (p.innerText || '').trim()));
      const price = ps.find(p => (p.innerText || '').includes('💸 '));
      if (imgs.length !== 2 || !product || !price) return false;
      const follows = (a,b) => !!(a.compareDocumentPosition(b) & 4);
      return follows(product,imgs[1]) && follows(imgs[1],price);
    }"""))


def publish_one(article: dict, state_secret: str | None = None,
                *, dry_run: bool = False) -> dict:
    """Attempt exactly one submission. Caller already persisted claim."""
    import os
    from playwright.sync_api import sync_playwright

    if os.getenv("DAANGN_CAFE_RESTORED") == "true":
        if not restored_live_gate():
            raise RuntimeError("DAANGN_CAFE_PARTNERS_PERMISSION_NOT_CONFIRMED")
        if os.getenv("DAANGN_CAFE_TARGET_URL") != RESTORED_CAFE:
            raise RuntimeError("DAANGN_CAFE_RESTORED_TARGET_MISMATCH")
        from gh_only.coupang_partners_api import issued_link_url
        receipt = article.get("official_link_receipt")
        if (not isinstance(receipt, dict)
                or receipt.get("issued_by") != "COUPANG_PARTNERS_API"
                or receipt.get("subid_requested") != "cafe"
                or receipt.get("partners_link") != article.get("candidate_url")
                or not issued_link_url(article.get("candidate_url"))):
            raise RuntimeError("DAANGN_CAFE_OFFICIAL_API_LINK_REQUIRED")
    target, prefix = target_for_cafe(
        os.getenv("DAANGN_CAFE_TARGET_URL",
                  "https://cafe.daangn.com/i-gagyeog-mos-c")
    )
    board_label = restored_board_label()
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
                from gh_only.lowchart_generated_price_chart import generate_price_chart_image
                chart = generate_price_chart_image(
                    browser, article.get("chart_identity"), article["image"].parent,
                    article.get("source_id"),
                )
                article["chart_image"] = chart
                result["chart_captured"] = chart is not None
                result["chart_kind"] = ("self_generated_price_summary"
                                        if chart is not None else None)
                result["failure_stage"] = "OPEN_DAANGN_PAGE"
                ctx = browser.new_context(
                    storage_state=state, locale="ko-KR",
                    viewport={"width": 1366, "height": 950},
                )
                page = ctx.new_page()
                resp = page.goto(target, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1800)
                result["http_status"] = resp.status if resp else None
                result["page_path"] = urlsplit(page.url).path
                if (not resp or resp.status != 200
                    or urlsplit(page.url).hostname != "cafe.daangn.com"
                    or urlsplit(page.url).path != prefix + "new"):
                    raise RuntimeError("EXPECTED_EDITOR_NOT_REACHED")
                result["failure_stage"] = "SELECT_BOARD"
                board = page.locator("select")
                if board.count() != 1:
                    raise RuntimeError("BOARD_NOT_UNIQUE")
                board.select_option(label=board_label)
                if board.locator("option:checked").inner_text().strip() != board_label:
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
                # Daangn's current image toolbar APPENDS a new image at the
                # end of ProseMirror regardless of the native caret position.
                # Therefore type the product name first; append its chart;
                # then focus the empty paragraph AFTER that chart and type
                # the remaining reference/price/notice/link/footer there.
                # Without an optional chart the original writing path remains.
                lines = article["body"].split("\n")
                if chart is None:
                    for i, line in enumerate(lines):
                        if line:
                            page.keyboard.insert_text(line)
                        if i < len(lines) - 1:
                            page.keyboard.press("Enter")
                else:
                    if not lines or not lines[0].strip():
                        raise RuntimeError("DAANGN_PRODUCT_NAME_LINE_MISSING")
                    page.keyboard.insert_text(lines[0])
                    result["failure_stage"] = "UPLOAD_OPTIONAL_PRICE_CHART"
                    add_image = page.get_by_role("button", name="이미지 추가")
                    if add_image.count() != 1:
                        raise RuntimeError("CHART_IMAGE_BUTTON_NOT_UNIQUE")
                    with page.expect_file_chooser(timeout=11000) as chooser:
                        add_image.click(timeout=7000)
                    chooser.value.set_files(str(chart.resolve()))
                    page.wait_for_function("""() => {
                      const e=document.querySelector('div.ProseMirror[contenteditable=true]');
                      const imgs=e && [...e.querySelectorAll('img')];
                      return imgs && imgs.length===2 && imgs.every(
                        img=>img.complete&&img.naturalWidth>0) &&
                        !(e.innerText||'').includes('준비중');
                    }""", timeout=35000)
                    result["chart_upload_completed"] = True
                    result["failure_stage"] = "FOCUS_PARAGRAPH_AFTER_CHART"
                    trailing = editor.locator("p").last
                    after_chart = editor.evaluate(r"""root => {
                      const imgs = [...root.querySelectorAll('img')];
                      const ps = [...root.querySelectorAll('p')];
                      if(imgs.length!==2 || !ps.length) return false;
                      const tail=ps[ps.length-1], img=imgs[1];
                      return !!(img.compareDocumentPosition(tail) & 4)
                        && !(tail.innerText||'').trim();
                    }""")
                    if not after_chart:
                        raise RuntimeError("DAANGN_NO_EMPTY_PARAGRAPH_AFTER_CHART")
                    trailing.click(timeout=7000)
                    result["failure_stage"] = "WRITE_EDITOR_BODY"
                    for index, line in enumerate(lines[1:]):
                        if line:
                            page.keyboard.insert_text(line)
                        if index < len(lines[1:]) - 1:
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
                    "disclosure_last": (
                        editor.evaluate(r"""e => [...e.children].filter(x=>x.matches('p'))
                            .map(x=>x.innerText.trim()).filter(Boolean).at(-1)""") == DISCLOSURE
                        if chart is not None else observed.rstrip().endswith(DISCLOSURE)
                    ),
                    "image_count": image.count(),
                    "chart_captured": chart is not None,
                    "chart_between_name_and_reference": (
                        _chart_between_product_and_reference(editor)
                        if chart is not None else True
                    ),
                    "observed_length": len(observed),
                    "expected_length": len(article["body"]),
                }
                if not validate_editor_copy(
                    editor, article["body"], article["candidate_url"], image,
                    chart_attached=chart is not None
                ) or not result["editor_checks"]["chart_between_name_and_reference"]:
                    raise RuntimeError("EDITOR_CONTENT_OR_FULL_URL_NOT_VERIFIED")
                if board.locator("option:checked").inner_text().strip() != board_label:
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
                if (image.count() != (2 if chart is not None else 1)
                        or not image.evaluate_all(
                            "(nodes)=>nodes.every(e=>e.complete&&e.naturalWidth>0)"
                        )):
                    raise RuntimeError("PHOTO_OR_CHART_MISSING_BEFORE_SUBMIT")
                # A React/editor rerender may split a URL after initial typing.
                # Refuse a submit unless the full link remains one paragraph.
                if not verify_link_in_one_paragraph(editor, article["candidate_url"]):
                    raise RuntimeError("PRODUCT_LINK_PARAGRAPH_SPLIT_DO_NOT_SUBMIT")
                # The deal may have aged past 60 minutes while the image
                # uploaded. Do not submit such a post, even if already claimed.
                if deal_expired_before_submit(article) and not dry_run:
                    raise RuntimeError("DEAL_EXPIRED_BEFORE_SUBMIT")
                if dry_run:
                    result["status"] = "EDITOR_DRY_RUN_VERIFIED_NO_SUBMIT"
                    result["failure_stage"] = "NO_SUBMIT_TEST_COMPLETE"
                    return result
                # No retry under ANY condition, including timeout or navigation error.
                result["failure_stage"] = "SUBMIT_CLICKED_OUTCOME_UNCERTAIN"
                result["submit_attempted"] = True
                submit.click(timeout=10000)
                page.wait_for_timeout(7500)
                dest = urlsplit(page.url)
                result["post_url"] = page.url if (
                    dest.scheme == "https"
                    and dest.hostname == "cafe.daangn.com"
                    and dest.path.startswith(prefix)
                    and dest.path != prefix + "new"
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
                    "(nodes,minCount)=>nodes.filter(e=>e.complete&&e.naturalWidth>0&&e.naturalHeight>0).length>=minCount",
                    2 if chart is not None else 1,
                )
                footer = all(term in content for term in (
                    "※ 실시간 핫딜 특성상",
                    "카페 가입 알림 설정하고 꿀템 챙겨가세요.",
                    DISCLOSURE,
                ))
                title_ok = post_title in content
                board_ok = "오늘의핫딜" in norm(content)
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
