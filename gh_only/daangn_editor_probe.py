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
    if parsed.scheme != "https" or parsed.hostname != "link.coupang.com" or parsed.path != "/re/AFFSDP":
        raise ValueError("DRAFT_AFFSDP_HOST_INVALID")
    aff = row.get("affsdp")
    if not isinstance(aff, dict) or aff.get("status") != "UNISSUED_AFFSDP_CANDIDATE":
        raise ValueError("DRAFT_LINK_NOT_CANDIDATE")
    if aff.get("commission_attribution_verified") is not False or aff.get("official_link_issued") is not False:
        raise ValueError("DRAFT_UNVERIFIED_STATUS_REQUIRED")
    original = row.get("source_post_url")  # Telegram post, NOT the source affiliate link
    if not isinstance(original, str) or not original.startswith("https://t.me/jirum/"):
        raise ValueError("DRAFT_SOURCE_UNEXPECTED")
    title_line = body.splitlines()[0].strip()
    if not title_line.startswith("📦 상품명 : "):
        raise ValueError("DRAFT_PRODUCT_NAME_MISSING")
    title = title_line.removeprefix("📦 상품명 : ").strip()
    if not (2 <= len(title) <= 150):
        raise ValueError("DRAFT_PRODUCT_NAME_INVALID")
    title = f"[오늘의 핫딜] {title}"[:150]
    photo = row.get("photo") if isinstance(row.get("photo"), dict) else {}
    filename = photo.get("saved_name")
    image = None
    if photo.get("status") == "IMAGE_SAVED":
        if (not isinstance(filename, str) or filename != Path(filename).name
                or Path(filename).suffix.lower() not in IMAGE_EXTS
                or not re.fullmatch(r"jirum_[0-9]+_kakao_og\.(?:jpg|jpeg|png|webp)", filename)):
            raise ValueError("DRAFT_IMAGE_NAME_INVALID")
        image = folder / filename
        if not image.is_file() or image.stat().st_size > 2_500_000:
            raise ValueError("DRAFT_IMAGE_NOT_PRESENT")
    # Keep disclosure explicit even in temporary editor content.
    clean_body = body + "\n\n" + DISCLOSURE
    return {
        "title": title,
        "body": clean_body,
        "image": image,
        "source_id": row.get("source_message_id"),
        "unissued_affsdp_candidate": True,
    }


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

    # Board selection is optional; do not infer the board or click unrelated controls.
    # Explicitly fail visibility if a target board control is not uniquely present.
    try:
        choices = page.get_by_text(board_name, exact=True)
        board_control = choose_visible_unique([choices])
        if board_control is not None:
            board_control.click(timeout=2500)
            result["board_selected"] = True
    except Exception:
        pass

    title = choose_visible_unique([
        page.locator('input[placeholder*="제목"]'),
        page.locator('textarea[placeholder*="제목"]'),
        page.locator('input[name="title"]'),
    ])
    if title is not None:
        title.fill(article["title"], timeout=5000)
        result["title_filled"] = title.input_value() == article["title"]
    editor = choose_visible_unique([
        page.locator('[contenteditable="true"][role="textbox"]'),
        page.locator('div[contenteditable="true"].ProseMirror'),
        page.locator('textarea[placeholder*="내용"]'),
    ])
    if editor is None:
        editor = choose_visible_unique([page.locator('[contenteditable="true"]')])
    if editor is not None:
        editor.fill(article["body"], timeout=7500)
        try:
            result["body_filled"] = article["body"] in editor.inner_text(timeout=1800)
        except Exception:
            try:
                result["body_filled"] = editor.input_value() == article["body"]
            except Exception:
                result["body_filled"] = False

    if article["image"] is not None:
        photo = choose_visible_unique([page.locator('input[type="file"][accept*="image"]')])
        if photo is None:
            # Hidden file inputs are valid targets for set_input_files.
            file_inputs = page.locator('input[type="file"][accept*="image"]')
            if file_inputs.count() == 1:
                photo = file_inputs.first
        if photo is not None:
            photo.set_input_files(str(article["image"]), timeout=8000)
            result["image_attached"] = True

    result["status"] = (
        "EDITOR_INPUTS_FILLED_DRY_RUN" if result["title_filled"] and result["body_filled"]
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
    # Dry-run failure states intentionally don't fail the workflow; evidence saved.


if __name__ == "__main__":
    main()
