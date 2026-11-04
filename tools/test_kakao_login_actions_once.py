"""One-shot authorized Kakao Developers login smoke test from GitHub Secrets.

This does NOT publish posts, inspect an account, store browser state, upload
screenshots, export cookies, or update any GitHub Secrets. Exactly one
credential submission is permitted; 2FA, verification and CAPTCHA stop safely.
Print ONLY an allowlisted result code; suppress exception details.
"""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

TARGET = "https://developers.kakao.com/tool/debugger/sharing"
OUT_PREFIX = "KAKAO_LOGIN_TEST_RESULT="

def emit(code: str) -> int:
    print(OUT_PREFIX + code, flush=True)
    return 0 if code in ("LOGIN_SUCCESS", "ALREADY_LOGGED_IN") else 1

def visible_first(page, selectors):
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None

def on_debugger(page):
    u = urlsplit(page.url)
    if u.scheme != "https" or u.hostname != "developers.kakao.com":
        return False
    if not u.path.startswith("/tool/debugger"):
        return False
    return visible_first(page, (
        'input[type="url"]',
        'input[placeholder*="URL"]',
        'input[placeholder*="url"]',
        'input[aria-label*="URL"]',
    )) is not None

def main() -> int:
    account_id = os.getenv("KAKAO_LOGIN_ID", "")
    password = os.getenv("KAKAO_LOGIN_PASSWORD", "")
    if not account_id or not password:
        return emit("MISSING_GITHUB_SECRET")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="ko-KR",
                    viewport={"width": 1280, "height": 900})
                page = ctx.new_page()
                page.goto(TARGET, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1200)
                if on_debugger(page):
                    return emit("ALREADY_LOGGED_IN")

                # Developers may render a login CTA before redirecting.
                if urlsplit(page.url).hostname == "developers.kakao.com":
                    targets = [
                        'a[href*="accounts.kakao.com"]',
                        'a[href*="login"]',
                        'button:has-text("로그인")',
                        'a:has-text("로그인")',
                    ]
                    button = visible_first(page, targets)
                    if button is not None:
                        button.click(timeout=6000)
                        page.wait_for_timeout(1200)
                        if on_debugger(page):
                            return emit("ALREADY_LOGGED_IN")

                host = urlsplit(page.url).hostname
                if host not in ("accounts.kakao.com", "kauth.kakao.com"):
                    return emit("KAKAO_LOGIN_FORM_NOT_REACHED")

                username_field = visible_first(page, (
                    'input[name="loginId"]',
                    'input[id="loginId"]',
                    'input[autocomplete="username"]',
                    'input[name="email"]',
                    'input[type="email"]',
                    'input[placeholder*="카카오계정"]',
                    'input[placeholder*="이메일"]',
                ))
                password_field = visible_first(page, (
                    'input[name="password"]',
                    'input[type="password"]',
                    'input[autocomplete="current-password"]',
                ))
                if username_field is None or password_field is None:
                    return emit("LOGIN_INPUTS_NOT_FOUND")

                username_field.fill(account_id, timeout=6000)
                password_field.fill(password, timeout=6000)
                submit = visible_first(page, (
                    'button[type="submit"]',
                    'button:has-text("로그인")',
                    'input[type="submit"]',
                ))
                if submit is None:
                    return emit("LOGIN_SUBMIT_BUTTON_NOT_FOUND")

                # ONE submission, no retries, no bypassing verification.
                submit.click(timeout=9000)
                for _ in range(10):
                    page.wait_for_timeout(1400)
                    if on_debugger(page):
                        return emit("LOGIN_SUCCESS")
                    if urlsplit(page.url).hostname == "developers.kakao.com":
                        try:
                            page.goto(TARGET, wait_until="domcontentloaded",
                                      timeout=10000)
                        except Exception:
                            pass
                        if on_debugger(page):
                            return emit("LOGIN_SUCCESS")

                # No body, URL query parameters, screenshots or session export.
                # Two-step verification is not automated.
                if urlsplit(page.url).hostname in ("accounts.kakao.com", "kauth.kakao.com"):
                    return emit("LOGIN_UNCONFIRMED_OR_ADDITIONAL_VERIFICATION")
                return emit("LOGIN_NOT_VERIFIED")
            finally:
                browser.close()
    except Exception:
        return emit("BROWSER_OR_SITE_ERROR")

if __name__ == "__main__":
    sys.exit(main())
