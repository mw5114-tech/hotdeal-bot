"""One-shot anonymous Coupang Partners UI discovery in a GitHub Actions browser.

Read-only: no credentials, login attempt, CAPTCHA interaction, affiliate link
generation, hidden API calls, anti-bot measures, or cookies archived.
Only official partners.coupang.com is navigated.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

ENTRY = "https://partners.coupang.com/"
OUT = Path("partners_anonymous_ui_diagnostic.json")
TERMS = ("간편 링크", "간편링크", "링크 생성", "로그인", "쿠팡 URL", "파트너스 URL",
         "회원가입", "로그아웃", "링크/배너")


def run():
    result = {
        "schema": "coupang-partners-anonymous-ui-v1",
        "entry": ENTRY,
        "status": "NOT_RUN",
        "login_attempted": False,
        "link_creation_attempted": False,
        "automated_login_bypass_attempted": False,
    }
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale="ko-KR")
                page = context.new_page()
                response = page.goto(ENTRY, wait_until="domcontentloaded", timeout=27000)
                try:
                    page.wait_for_load_state("networkidle", timeout=9000)
                except PWTimeout:
                    pass
                where = urlsplit(page.url)
                result["status"] = "PAGE_OPENED"
                result["http_status"] = response.status if response else None
                result["final_host"] = where.hostname
                result["final_path"] = where.path
                result["page_title"] = page.title()[:140]
                text = " ".join(page.locator("body").inner_text(timeout=8000).split())
                result["body_text_length"] = len(text)
                result["ui_labels"] = [word for word in TERMS if word in text]
                result["link_create_button_count"] = page.get_by_role("button", name="링크 생성", exact=True).count()
                result["input_placeholder_labels"] = [
                    str(x)[:90] for x in page.locator("input[placeholder]").evaluate_all(
                        "(els) => els.map(el => el.placeholder).slice(0,15)"
                    )
                ]
                result["main_text_sample"] = text[:280]
                # Never include tokens/cookies or full URL query in reports.
                result["likely_login_required"] = (
                    "login" in (where.path or "").lower()
                    or "로그인" in text and "로그아웃" not in text
                )
                result["verified_usable_affiliate_creator"] = False
                context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["status"] = "BROWSER_ERROR"
        result["error_type"] = type(exc).__name__
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("COUPANG_PARTNERS_ANON_UI " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    run()
