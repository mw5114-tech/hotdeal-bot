"""One-shot public Coupang Partners login-form accessibility diagnostic.

A fresh GitHub-hosted Chromium visits the public Partners homepage and clicks
the visible, user-facing 'login' button at most once. No sign-in, CAPTCHA
handling, retries, proxy rotation, credentials, cookies or session exports.
This test checks whether a blank login form appears; it does not establish a
working or persistent authenticated session.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ENTRY = "https://partners.coupang.com/"
DENIED = re.compile(
    r"access\s*denied|access\s*blocked|forbidden|request\s*blocked|"
    r"사용\s*권한이\s*없|접근[이\s]*거부|접근[이\s]*차단|"
    r"captcha|verify\s*you\s*are\s*human|로봇이\s*아닙니다", re.I,
)
LOGIN_NAME = re.compile(r"로그인|log\s*in|sign\s*in", re.I)


def safe_location(value: str) -> dict:
    """Never log query strings, fragments, cookies or return URLs."""
    try:
        uri = urlsplit(value)
        return {"host": uri.hostname or "", "path": uri.path[:140]}
    except ValueError:
        return {"host": "INVALID", "path": ""}


def classify(*, page_url: str, status: int | None, visible_text: str,
             password_visible: bool, login_clicked: bool,
             login_button_found: bool) -> str:
    host = safe_location(page_url)["host"]
    if password_visible and host in {"login.coupang.com", "partners.coupang.com"}:
        return "LOGIN_FORM_VISIBLE"
    if status in (401, 403, 429) or DENIED.search(visible_text):
        return "ACCESS_DENIED_OR_CHALLENGE"
    if host == "login.coupang.com":
        return "LOGIN_PAGE_NO_VISIBLE_PASSWORD_FIELD"
    if not login_clicked and not login_button_found:
        return "LOGIN_BUTTON_NOT_FOUND"
    if not login_clicked:
        return "LOGIN_CLICK_FAILED"
    return "LOGIN_FORM_NOT_REACHED"


def run(out_dir: Path) -> dict:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "coupang-login-form-github-one-shot-v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": "github-hosted-ubuntu-headless-chromium",
        "homepage_status": None,
        "last_navigation_status": None,
        "login_button_found": False,
        "login_clicked": False,
        "password_field_visible": False,
        "classification": "PROBE_ERROR",
        "location": {"host": "", "path": ""},
        "diagnostic_error_type": None,
        "login_attempted": False,
        "credentials_used": False,
        "captcha_attempted": False,
        "retry_count": 0,
    }
    page = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, timeout=30000)
            try:
                ctx = browser.new_context(accept_downloads=False)
                page = ctx.new_page()
                statuses = []

                def record(response):
                    try:
                        if response.request.is_navigation_request() and response.frame == page.main_frame:
                            statuses.append(response.status)
                    except Exception:
                        pass

                page.on("response", record)
                response = page.goto(ENTRY, wait_until="domcontentloaded", timeout=30000)
                result["homepage_status"] = response.status if response else None
                page.wait_for_timeout(1800)

                if not page.locator("input[type='password']:visible").count():
                    for kind in ("link", "button"):
                        options = page.get_by_role(kind, name=LOGIN_NAME)
                        for idx in range(min(options.count(), 8)):
                            item = options.nth(idx)
                            if item.is_visible():
                                result["login_button_found"] = True
                                try:
                                    item.click(timeout=10000)
                                    result["login_clicked"] = True
                                except Exception as exc:
                                    result["diagnostic_error_type"] = type(exc).__name__
                                break
                        if result["login_button_found"]:
                            break
                    if result["login_clicked"]:
                        page.wait_for_timeout(2800)
                        # Some pages open login in a new tab; inspect that
                        # tab without initiating any additional navigation.
                        if len(ctx.pages) > 1:
                            page = ctx.pages[-1]
                            page.wait_for_load_state("domcontentloaded", timeout=12000)

                result["password_field_visible"] = (
                    page.locator("input[type='password']:visible").count() > 0
                )
                try:
                    title = page.title()
                    visible_text = title + "\n" + page.locator("body").inner_text(timeout=4000)[:6500]
                except Exception:
                    visible_text = ""
                result["location"] = safe_location(page.url)
                result["last_navigation_status"] = statuses[-1] if statuses else result["homepage_status"]
                result["classification"] = classify(
                    page_url=page.url,
                    status=result["last_navigation_status"],
                    visible_text=visible_text,
                    password_visible=result["password_field_visible"],
                    login_clicked=result["login_clicked"],
                    login_button_found=result["login_button_found"],
                )
                # Only unauthenticated screens. Password/credentials are
                # never entered. The screenshot contains no stored session.
                try:
                    page.screenshot(path=str(out_dir / "public-login-page.png"),
                                    full_page=False, timeout=8000)
                except Exception as exc:
                    result["screenshot_error_type"] = type(exc).__name__
            finally:
                browser.close()
    except Exception as exc:
        result["classification"] = "PROBE_ERROR"
        result["diagnostic_error_type"] = type(exc).__name__

    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("COUPANG_LOGIN_FORM_RESULT " + json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="coupang-login-form-diagnostic")
    args = parser.parse_args()
    out = run(Path(args.out_dir))
    # An access denial is a useful result, not a failed infrastructure run.
    return 1 if out["classification"] == "PROBE_ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
