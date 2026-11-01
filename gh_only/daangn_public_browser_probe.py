"""One anonymous public Daangn Cafe landing page browser reachability check."""
from __future__ import annotations
import json
from playwright.sync_api import sync_playwright

if __name__ == "__main__":
    out = {"public_page": "https://cafe.daangn.com/", "login_attempted": False,
           "session_loaded": False, "post_published": False}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                response = page.goto("https://cafe.daangn.com/", wait_until="domcontentloaded",
                                     timeout=25000)
                out["http_status"] = response.status if response else None
                out["page_title_present"] = bool(page.title().strip())
                out["has_login_text"] = page.get_by_text("로그인", exact=True).count() > 0
                out["public_browser_reachable"] = out["http_status"] == 200
            finally:
                browser.close()
    except Exception as e:
        out.update({"public_browser_reachable": False, "error_type": type(e).__name__})
    print("DAANGN_PUBLIC_BROWSER " + json.dumps(out, ensure_ascii=False))
