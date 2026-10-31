"""Opt-in read-only authenticated Coupang Partners UI check on GitHub.

Uses a user-authorized Playwright storage_state GitHub Secret, scoped to
coupang.com domains. NEVER submits a login form, creates links, clicks CAPTCHA,
screenshots, exports cookie values, or records account identifiers. A failure to
reach the UI is reported as a reason, not an auth bypass task.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

TOOL = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
SECRET_ENV = "COUPANG_PARTNERS_STORAGE_STATE_JSON"
OUT = Path("partners_authenticated_access_probe.json")


def is_coupang_domain(value: object) -> bool:
    if not isinstance(value, str):
        return False
    host = value.lstrip(".").casefold()
    return host == "coupang.com" or host.endswith(".coupang.com")


def safe_coupang_state(raw: str) -> dict:
    state = json.loads(raw)
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise ValueError("NOT_PLAYWRIGHT_STORAGE_STATE")
    if not isinstance(state.get("origins", []), list):
        raise ValueError("BAD_ORIGINS")
    if any(not isinstance(x, dict) for x in state["cookies"]):
        raise ValueError("BAD_COOKIE_ENTRIES")
    cookies = [x for x in state["cookies"] if is_coupang_domain(x.get("domain"))]
    if not cookies:
        raise ValueError("NO_COUPANG_SESSION_COOKIES")
    origins = []
    for entry in state.get("origins", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("origin"), str):
            continue
        try:
            p = urlsplit(entry["origin"])
            if (p.scheme == "https" and p.username is None
                    and p.password is None and is_coupang_domain(p.hostname)):
                origins.append(entry)
        except ValueError:
            continue
    return {"cookies": cookies, "origins": origins}


def run():
    r = {"schema": "partners-github-authorized-session-ui-check.v1",
         "status": "NOT_RUN", "session_values_output": False,
         "login_attempted": False, "links_created": 0,
         "anti_bot_bypass_attempted": False,
         "input_field_visible": False, "quick_link_text_visible": False}
    raw = os.getenv(SECRET_ENV, "")
    if not raw:
        r["status"] = "SESSION_SECRET_MISSING"
    else:
        try:
            state = safe_coupang_state(raw)
        except Exception as exc:
            r["status"] = "INVALID_OR_NO_SCOPE_SESSION"
            r["validation_error_type"] = type(exc).__name__
        else:
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    try:
                        ctx = browser.new_context(locale="ko-KR",storage_state=state)
                        page = ctx.new_page()
                        response = page.goto(TOOL, wait_until="domcontentloaded",timeout=25000)
                        try:
                            page.wait_for_load_state("networkidle",timeout=9000)
                        except PWTimeout:
                            pass
                        u = urlsplit(page.url)
                        text = " ".join(page.locator("body").inner_text(timeout=8000).split())
                        status = response.status if response else None
                        r["http_status"] = status
                        r["final_host"] = u.hostname
                        r["final_path"] = u.path
                        r["access_denied"] = status == 403 or "access denied" in text.casefold()
                        r["login_visible"] = "로그인" in text and "로그아웃" not in text
                        r["quick_link_text_visible"] = (
                            "간편 링크" in text or "간편링크" in text
                        )
                        r["input_field_visible"] = (
                            page.locator('input[placeholder*="쿠팡"]').count() > 0
                            or page.locator('input[placeholder*="URL"]').count() > 0
                        )
                        if r["access_denied"]:
                            r["status"] = "ACCESS_DENIED_EVEN_WITH_SESSION"
                        elif u.hostname != "partners.coupang.com":
                            r["status"] = "LOGIN_REDIRECT_OR_OTHER_HOST"
                        elif r["quick_link_text_visible"] and r["input_field_visible"]:
                            r["status"] = "QUICK_LINK_FORM_REACHABLE"
                        elif r["login_visible"]:
                            r["status"] = "SESSION_EXPIRED_OR_REJECTED"
                        else:
                            r["status"] = "PAGE_REACHED_BUT_UI_UNCONFIRMED"
                        ctx.close()
                    finally:
                        browser.close()
            except Exception as exc:
                r["status"] = "BROWSER_ERROR"
                r["error_type"] = type(exc).__name__
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PARTNERS_SESSION_PROBE " + json.dumps(r,ensure_ascii=False))


if __name__ == "__main__":
    run()
