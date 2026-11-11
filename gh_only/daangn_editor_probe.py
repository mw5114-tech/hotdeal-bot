"""Single-item Daangn Cafe browser editor DRY RUN from generated Jirum evidence.

Never clicks Publish/Register/Submit, never sends any HTTP write intentionally,
never claims a persistent draft. Site may autosave editor state on its own.
Loads owner-created Playwright storage state from an Actions secret at runtime.
No auth credentials/cookies are written to logs, screenshots or artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

CAFE_HOST = "cafe.daangn.com"
ALLOWED_AUTH_ROOT = "daangn.com"
CAFE_PATH = re.compile(r"^/[A-Za-z0-9_-]+(?:/posts)?/?$")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
MAX_SESSION_SIZE = 750_000
MAX_DRAFT_LEN = 7000


def validate_cafe_url(raw: str) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 500:
        raise ValueError("TARGET_CAFE_URL_REQUIRED")
    try:
        u = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("INVALID_CAFE_URL") from exc
    if (u.scheme != "https" or u.hostname != CAFE_HOST or u.port not in (None, 443)
            or u.username or u.password or u.fragment or u.query or not CAFE_PATH.fullmatch(u.path)):
        raise ValueError("INVALID_CAFE_URL")
    slug = u.path.split("/")[1]
    return f"https://{CAFE_HOST}/{slug}"


def _allowed_domain(domain: str | None) -> bool:
    return isinstance(domain, str) and (domain.lstrip(".").lower() == ALLOWED_AUTH_ROOT
                                        or domain.lstrip(".").lower().endswith("." + ALLOWED_AUTH_ROOT))


def parse_daangn_storage_state(secret: str) -> dict:
    if not isinstance(secret, str) or not (3 <= len(secret) <= MAX_SESSION_SIZE):
        raise ValueError("DAANGN_SESSION_EMPTY_OR_TOO_BIG")
    try:
        obj = json.loads(secret)
    except (ValueError, TypeError) as exc:
        raise ValueError("DAANGN_SESSION_BAD_JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("DAANGN_SESSION_BAD_STRUCTURE")
    cookies = obj.get("cookies")
    origins = obj.get("origins")
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("DAANGN_SESSION_BAD_STRUCTURE")
    if any(not isinstance(c, dict) for c in cookies) or any(not isinstance(o, dict) for o in origins):
        raise ValueError("DAANGN_SESSION_BAD_STRUCTURE")
    safe_cookies = [c for c in cookies if _allowed_domain(c.get("domain"))]
    safe_origins = []
    for origin in origins:
        src = origin.get("origin")
        try:
            p = urlsplit(src) if isinstance(src, str) else None
        except ValueError:
            p = None
        if p and p.scheme == "https" and _allowed_domain(p.hostname):
            safe_origins.append(origin)
    if not safe_cookies and not safe_origins:
        raise ValueError("DAANGN_SESSION_NO_SITE_ENTRIES")
    return {"cookies": safe_cookies, "origins": safe_origins}


def read_one_draft(folder: Path) -> dict:
    """Accept only one valid product draft and only its same-source local photo."""
    try:
        report = json.loads((folder / "drafts.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("DRAFT_JSON_UNAVAILABLE") from exc
    if report.get("schema") != "github-jirum-default-price-photo-affsdp-draft-v2":
        raise ValueError("DRAFT_SCHEMA_UNEXPECTED")
    drafts = report.get("drafts")
    if not isinstance(drafts, list) or len(drafts) != 1:
        raise ValueError("EXACTLY_ONE_DRAFT_REQUIRED")
    row = drafts[0]
    if not isinstance(row, dict):
        raise ValueError("INVALID_DRAFT")
    caption = row.get("caption")
    if not isinstance(caption, dict):
        raise ValueError("DRAFT_CAPTION_MISSING")
    body = caption.get("caption")
    url = row.get("composed_affiliate_url")
    if (not isinstance(body, str) or not (12 <= len(body) <= MAX_DRAFT_LEN)
            or not isinstance(url, str) or body.count(url) != 1
            or not body.rstrip().endswith(url)):
        raise ValueError("DRAFT_AFFSDP_LINK_MISSING_OR_AMBIGUOUS")
    parsed = urlsplit(url)
    aff = row.get("affsdp")
    receipt = row.get("official_link_receipt")
    if receipt is not None:
        from gh_only.official_link_receipts import official_url
        # Only the Naver path can create this API-backed receipt; do not
        # loosen validation of existing Muse /a/ receipts for Daangn.
        if isinstance(receipt, dict) and receipt.get("issued_by") == "COUPANG_PARTNERS_API":
            from gh_only.coupang_partners_api import issued_link_url
            valid_issued = (issued_link_url(url)
                            and receipt.get("subid_requested") == "naverblog")
        else:
            valid_issued = official_url(url)
        if (not isinstance(receipt, dict)
                or not valid_issued
                or receipt.get("partners_link") != url):
            raise ValueError("DRAFT_OFFICIAL_LINK_INVALID")
        if (not isinstance(aff, dict)
                or aff.get("status") != "OFFICIAL_PARTNERS_LINK_RECEIPT"
                or aff.get("official_link_issued") is not True
                or aff.get("commission_attribution_verified") is not False):
            raise ValueError("DRAFT_OFFICIAL_RECEIPT_METADATA_INVALID")
    elif row.get("muse_rewrite_pending") is True:
        from gh_only.direct_product_links import verify_regular_product_url
        if not verify_regular_product_url(url, row.get("product_identity", {})):
            raise ValueError("DRAFT_DIRECT_PRODUCT_LINK_INVALID")
        if (not isinstance(aff, dict)
                or aff.get("status") != "REGULAR_PRODUCT_AWAITING_MUSE"
                or aff.get("official_link_issued") is not False
                or aff.get("commission_attribution_verified") is not False):
            raise ValueError("DRAFT_MUSE_REWRITE_STATE_INVALID")
    else:
        # Original AFFSDP composition is active when both optional Muse and
        # official-receipt production modes are explicitly disabled.
        if (parsed.scheme != "https" or parsed.hostname != "link.coupang.com"
                or parsed.path != "/re/AFFSDP"):
            raise ValueError("DRAFT_AFFSDP_HOST_INVALID")
        if not isinstance(aff, dict) or aff.get("status") != "UNISSUED_AFFSDP_CANDIDATE":
            raise ValueError("DRAFT_LINK_NOT_CANDIDATE")
        if aff.get("commission_attribution_verified") is not False or aff.get("official_link_issued") is not False:
            raise ValueError("DRAFT_UNVERIFIED_STATUS_REQUIRED")
    original = row.get("source_post_url")  # Telegram post, NOT the source affiliate link
    if not isinstance(original, str) or not original.startswith("https://t.me/jirum/"):
        raise ValueError("DRAFT_SOURCE_UNEXPECTED")
    title_line = body.splitlines()[0].strip()
    # The on-page discount refers to the explicitly labelled comparison
    # source (Lowchart average OR Fallcent midpoint/high/current/low). Its
    # source-specific historical-mean metric stays None for non-mean sources.
    discount = caption.get("approx_discount_vs_reference_pct")
    # A source-verified discount is rounded to 5 points; never use a Telegram
    # merchant's unaudited reference price to invent a percentage.
    known_discount = type(discount) is int and discount in range(5, 100, 5)
    body_prefix = (f"[{discount}%할인] " if known_discount
                   else "[할인율 확인 중] ")
    if not title_line.startswith(body_prefix):
        raise ValueError("DRAFT_DISCOUNT_PREFIX_MISSING_OR_MISMATCHED")
    title = title_line[len(body_prefix):].strip()
    if not (2 <= len(title) <= 150):
        raise ValueError("DRAFT_PRODUCT_NAME_INVALID")
    # Retain existing Daangn POST title format: name THEN discount.
    if known_discount:
        suffix = f" [{discount}%할인]"
        if not title.endswith(suffix):
            title = title[:150-len(suffix)].rstrip() + suffix
    # Keep the requested "[쿠팡🔥핫딜] 제품명 [NN%할인]" format.
    # If a long product title exceeds the document cap, preserve the final
    # discount token rather than slicing it off.
    prefix = "[쿠팡🔥핫딜] "
    complete_title = prefix + title
    if len(complete_title) > 150:
        match = re.search(r" (\[\d+%할인\])$", title)
        if match:
            end = match.group(0)
            name = title[:-len(end)]
            complete_title = prefix + name[:150-len(prefix)-len(end)].rstrip() + end
        else:
            complete_title = complete_title[:150].rstrip()
    title = complete_title
    photo = row.get("photo") if isinstance(row.get("photo"), dict) else {}
    filename = photo.get("saved_name")
    image = None
    if photo.get("status") == "IMAGE_SAVED":
        if (not isinstance(filename, str) or filename != Path(filename).name
                or Path(filename).suffix.lower() not in IMAGE_EXTS
                or not re.fullmatch(r"jirum_[0-9]+_(?:kakao_og|lowchart_photo|telegram_source_photo|couspi_photo|dangapick_photo|fallcent_photo|naver_web_photo)\.(?:jpg|jpeg|png|webp)", filename)):
            raise ValueError("DRAFT_IMAGE_NAME_INVALID")
        image = folder / filename
        if not image.is_file() or image.stat().st_size > 2_500_000:
            raise ValueError("DRAFT_IMAGE_NOT_PRESENT")
    # Public discount language is validated separately. Only describe a
    # historical-low deal when its observed low is actually >= the deal price.
    stock_notice = ("※ 실시간 핫딜 특성상 준비된 재고가 소진되면 "
                    "가격이 원래대로 인상되거나 품절될 수 있습니다.")
    verified_claim = ("🔥 정가 뻥튀기 같은 가짜 할인은 거르고, 검증 마친 찐 역대가 딜만 가져옵니다. "
                      "품절되기 전에 카페 가입 알림 설정하고 꿀템 챙겨가세요.")
    unverified_claim = ("🔥 핫딜 정보를 확인해 소개합니다. "
                        "품절되기 전에 카페 가입 알림 설정하고 꿀템 챙겨가세요.")
    promo = (verified_claim if row.get("observed_low_verified_for_deal") is True
             else unverified_claim)
    # The link is ONE exact text paragraph, with no intervening blank line
    # between its label and URL, or immediately before/after the link row.
    # This also prevents an extra empty ProseMirror paragraph around the URL
    # from confusing Muse's later link-only edit.
    link_line = "🛒 쿠팡에서 상품정보 더보기 : " + url
    if body.count(link_line) != 1:
        raise ValueError("DRAFT_LINK_ROW_NOT_SINGLE_LINE")
    before, after = body.split(link_line, 1)
    if after.strip() or not before.endswith("\n\n"):
        raise ValueError("DRAFT_LINK_ROW_LAYOUT_INVALID")
    compact_body = before.rstrip("\n") + "\n" + link_line
    # One Enter around the link block, while retaining the wider disclosure gap.
    if row.get("image_source") == "fallcent_search_representative":
        image_notice = "\n※ 이미지는 폴센트 상품 검색 대표 사진으로, 실제 상품 및 옵션과 다를 수 있습니다."
    elif row.get("image_source") == "naver_web_first_image":
        image_notice = "\n※ 이미지는 네이버 이미지 검색 대표 사진으로, 실제 상품 및 옵션과 다를 수 있습니다."
    else:
        image_notice = ""
    clean_body = compact_body + "\n" + stock_notice + image_notice + "\n\n" + promo + "\n\n\n\n" + DISCLOSURE
    return {
        "title": title,
        "body": clean_body,
        "image": image,
        "source_id": row.get("source_message_id"),
        "unissued_affsdp_candidate": receipt is None and row.get("muse_rewrite_pending") is not True,
        "muse_rewrite_pending": row.get("muse_rewrite_pending") is True,
        "official_link_receipt": receipt,
        "candidate_url": url,
    }


def verify_link_in_one_paragraph(editor, url: str) -> bool:
    """A soft-wrapped URL is fine; an actual inserted paragraph break is not.

    ProseMirror may auto-linkify the text. If it does, its clickable href must
    also match the full intended URL. No live edit is performed by this check.
    """
    return bool(editor.evaluate("""(root, url) => {
      const line = '🛒 쿠팡에서 상품정보 더보기 : ' + url;
      const nodes = [...root.querySelectorAll('p')];
      const matches = nodes.filter(p => p.textContent.trim() === line);
      if (matches.length !== 1) return false;
      const anchors = [...matches[0].querySelectorAll('a')];
      return anchors.every(a => a.getAttribute('href') === url);
    }""", url))


def choose_visible_unique(locators):
    """Prefer first selector with exactly one visible target.

    Avoid ambiguity *within* one selector. The same input may legitimately
    match multiple alternative selectors, so deduplicating by selector order
    rather than counting across selectors avoids false negatives.
    """
    for locator in locators:
        visible = []
        try:
            n = locator.count()
            if n > 8:
                continue
            for i in range(n):
                entry = locator.nth(i)
                if entry.is_visible(timeout=1200):
                    visible.append(entry)
        except Exception:
            continue
        if len(visible) == 1:
            return visible[0]
    return None


def try_open_editor(page) -> bool:
    choices = [
        page.get_by_role("button", name=re.compile(r"^(?:글쓰기|글 작성|게시글 작성|새 글 쓰기)$")),
        page.get_by_role("link", name=re.compile(r"^(?:글쓰기|글 작성|게시글 작성|새 글 쓰기)$")),
        page.get_by_text("글쓰기", exact=True),
    ]
    for choice in choices:
        loc = choose_visible_unique([choice])
        if loc is not None:
            loc.click(timeout=5000)
            return True
    return False


def prepare_browser_editor(page, article: dict, board_name: str = "오늘의 핫딜") -> dict:
    """Fill fields without clicking ANY publish button; report which worked."""
    result = {"editor_opened": False, "board_selected": False, "title_filled": False,
              "body_filled": False, "image_attached": False, "submit_clicked": False}
    if not try_open_editor(page):
        result["status"] = "WRITE_BUTTON_NOT_FOUND_OR_LOGIN_REQUIRED"
        return result
    result["editor_opened"] = True
    try:
        page.wait_for_load_state("domcontentloaded", timeout=4000)
    except Exception:
        pass

    # Pick the exact actual board option. The tested cafe uses the label
    # "🔥 오늘의 핫딜"; never keep the default "자유 게시판".
    select = page.locator("select")
    if select.count() == 1:
        options = select.locator("option").all_inner_texts()
        matching = [name for name in options if name.strip() == board_name or
                    name.strip().endswith(" " + board_name)]
        if len(matching) == 1:
            select.select_option(label=matching[0])
            result["board_selected"] = select.locator("option:checked").inner_text().strip() == matching[0].strip()
    if not result["board_selected"]:
        result["status"] = "TARGET_HOTDEAL_BOARD_NOT_SELECTED"
        return result

    editor = page.locator("div.ProseMirror[contenteditable=true]")
    if editor.count() != 1:
        result["status"] = "RICH_EDITOR_UNAVAILABLE"
        return result

    # PHOTO FIRST. The earlier image-after-text implementation inserted
    # the photo into the middle of the URL, splitting a long itemId.
    if article["image"] is None:
        result["status"] = "PRODUCT_PHOTO_MISSING"
        return result
    image_button = page.get_by_role("button", name="이미지 추가")
    if image_button.count() != 1:
        result["status"] = "IMAGE_ADD_CONTROL_AMBIGUOUS"
        return result
    with page.expect_file_chooser(timeout=11000) as picker:
        image_button.click(timeout=7000)
    picker.value.set_files(str(article["image"]))
    image = editor.locator("img")
    image.first.wait_for(state="visible", timeout=18000)
    page.wait_for_function("""() => {
      const node=document.querySelector('div.ProseMirror[contenteditable=true] img');
      return !!node && node.complete && node.naturalWidth > 0;
    }""", timeout=18000)
    result["image_attached"] = image.count() == 1 and image.first.evaluate("(el)=>el.naturalWidth>0")
    if not result["image_attached"]:
        result["status"] = "PHOTO_NOT_READY_BEFORE_TEXT"
        return result

    # TEXT SECOND. NEVER call editor.fill() after uploading an image; it
    # replaces rich editor nodes. Append one paragraph at a time instead.
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

    title = page.locator('input[placeholder="제목을 입력해주세요."]')
    if title.count() == 1:
        title.fill(article["title"])
        result["title_filled"] = title.input_value() == article["title"]

    actual = editor.inner_text(timeout=5000).strip()
    expected = article["body"].strip()
    normalize = lambda value: re.sub(r"\s+", "", value)
    result["body_filled"] = normalize(actual) == normalize(expected)
    url = article["candidate_url"]
    result["owner_url_above_disclosure"] = (
        actual.endswith(DISCLOSURE)
        and actual.count(url) == 1
        and actual.index(url) < actual.rfind(DISCLOSURE)
    )
    result["disclosure_is_last_line"] = bool(
        actual.splitlines() and actual.splitlines()[-1].strip() == DISCLOSURE
    )
    result["url_intact_in_one_text_node"] = editor.evaluate("""(node,url) => {
       const walker=document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
       let text;while((text=walker.nextNode())) {
         if(text.textContent.includes(url)) return true;
       }
       return false;
    }""", url)
    result["link_row_contiguous"] = verify_link_in_one_paragraph(editor, url)
    result["image_survived_body"] = image.count() == 1 and image.first.evaluate(
        "(el)=>el.complete&&el.naturalWidth>0")
    result["status"] = (
        "EDITOR_INPUTS_FILLED_DRY_RUN"
        if all([result["board_selected"], result["title_filled"],
                result["body_filled"], result["image_attached"],
                result["owner_url_above_disclosure"],
                result["disclosure_is_last_line"], result["url_intact_in_one_text_node"],
                result["image_survived_body"], result["link_row_contiguous"]])
        else "EDITOR_FIELDS_NOT_CONFIRMED"
    )
    return result


def open_daangn_one_draft(target: str, state_json: str | None, draft: dict,
                          output_dir: Path, board_name: str) -> dict:
    from playwright.sync_api import sync_playwright
    result = {
        "status": "NOT_STARTED",
        "source_message_id": draft["source_id"],
        "session_authenticated": False,
        "site_loaded": False,
        "post_published": False,
        "persisted_draft_verified": False,
        "submit_clicked": False,
        "editor_fields": {},
    }
    if not state_json:
        return {**result, "status": "DAANGN_LOGIN_SESSION_SECRET_MISSING"}
    try:
        state = parse_daangn_storage_state(state_json)
    except ValueError as exc:
        return {**result, "status": str(exc)}
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(storage_state=state, locale="ko-KR",
                                          viewport={"width": 1366, "height": 900})
                page = ctx.new_page()
                resp = page.goto(target, wait_until="domcontentloaded", timeout=25000)
                host = urlsplit(page.url).hostname
                result["http_status"] = resp.status if resp else None
                result["redirect_host"] = host
                if host != CAFE_HOST:
                    result["status"] = "SITE_REDIRECTED_TO_LOGIN_OR_UNKNOWN_HOST"
                    return result
                if result["http_status"] != 200:
                    result["status"] = "CAFE_PAGE_HTTP_ERROR"
                    return result
                result["site_loaded"] = True
                try:
                    page.wait_for_timeout(1500)
                    fields = prepare_browser_editor(page, draft, board_name)
                except Exception as exc:
                    fields = {"status": "EDITOR_ACTION_FAILED",
                              "error_type": type(exc).__name__, "submit_clicked": False}
                result["editor_fields"] = fields
                result["session_authenticated"] = fields.get("editor_opened", False)
                result["status"] = fields.get("status", "EDITOR_UNKNOWN")
                # Screenshot is in a private artifact, never session JSON or HTML.
                page.screenshot(path=str(output_dir / "daangn_editor_dry_run.png"),
                                full_page=False, timeout=8500)
                return result
            finally:
                browser.close()
    except Exception as exc:
        return {**result, "status": "BROWSER_SESSION_ERROR", "error_type": type(exc).__name__}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cafe-url", required=True)
    ap.add_argument("--drafts-dir", default="jirum-affsdp-drafts")
    ap.add_argument("--output-dir", default="daangn-editor-probe")
    ap.add_argument("--board-name", default="오늘의 핫딜")
    args = ap.parse_args()
    result_folder = Path(args.output_dir)
    result_folder.mkdir(parents=True, exist_ok=True)
    try:
        dest = validate_cafe_url(args.cafe_url)
        draft = read_one_draft(Path(args.drafts_dir))
        r = open_daangn_one_draft(dest, os.getenv("DAANGN_CAFE_STORAGE_STATE_JSON"),
                                  draft, result_folder, args.board_name)
    except ValueError as exc:
        r = {"status": str(exc), "post_published": False,
             "persisted_draft_verified": False, "submit_clicked": False}
    (result_folder / "status.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("DAANGN_EDITOR_PROBE " + json.dumps(r, ensure_ascii=False))
    # The private artifact is uploaded with 'if: always()' even if this job
    # fails. Do not show a green check when login, editor or photo failed.
    ok = (r.get("status") == "EDITOR_INPUTS_FILLED_DRY_RUN"
          and r.get("editor_fields", {}).get("image_attached") is True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
