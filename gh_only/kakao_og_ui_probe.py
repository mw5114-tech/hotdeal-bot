"""Isolated Kakao Developers OG metadata tool read-only browser feasibility probe.

Goal: determine if GitHub-hosted Chromium can reach the OG debugger without a
logged-in Kakao session. Never logs in, enters credentials, posts, resets cache,
or accesses Coupang product pages.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, TimeoutError as NavigationTimeout

TOOL_URL = "https://developers.kakao.com/tool/debugger"
OUT = Path("kakao_og_tool_browser_diagnostic.json")

def run():
    report = {
        "schema": "kakao-og-tool-public-access.v1",
        "requested_origin": "developers.kakao.com",
        "login_attempted": False,
        "credentials_used": False,
        "coupang_requests": 0,
        "metadata_submitted": False,
        "page_http_status": None,
        "final_host": None,
        "final_path": None,
        "state": "NOT_TESTED",
        "details": None,
    }
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale="ko-KR")
                page = context.new_page()
                try:
                    response = page.goto(TOOL_URL, wait_until="domcontentloaded", timeout=22000)
                    if response:
                        report["page_http_status"] = response.status
                    dest = urlsplit(page.url)
                    report["final_host"] = dest.hostname
                    report["final_path"] = dest.path
                    # Never log URL queries; authorization codes and tokens could live there.
                    if dest.hostname == "accounts.kakao.com" or "/login" in dest.path:
                        report["state"] = "KAKAO_LOGIN_REQUIRED"
                    elif dest.hostname == "developers.kakao.com":
                        report["state"] = "TOOL_PAGE_REACHABLE_UNAUTHENTICATED"
                        report["details"] = {
                            "page_title": page.title()[:120],
                            "url_input_count": page.locator('input[type="url"], input[placeholder*="URL"]').count(),
                            "metadata_lookup_buttons": page.get_by_text("메타 정보 조회", exact=False).count(),
                        }
                    else:
                        report["state"] = "UNEXPECTED_REDIRECT"
                except NavigationTimeout:
                    report["state"] = "TIMEOUT"
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        report["state"] = "ERROR"
        report["details"] = {"exception_type": type(exc).__name__}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print("KAKAO_OG_TOOL_ACCESS " + json.dumps(report, ensure_ascii=False))

if __name__ == "__main__":
    run()
