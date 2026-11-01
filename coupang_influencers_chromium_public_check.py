"""Actual Chromium public GUI availability check of Coupang Influencers.

Read-only, no account, cookies, login, CTA clicks, scraping or link generation.
A 200 from the HTTP client is not sufficient: visible browser text is required.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

URL = "https://influencers.coupang.com/"
DENIAL = ("access denied", "request blocked", "forbidden", "captcha")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, timeout=30000)
        try:
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            try:
                response = page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1700)
                body = page.locator("body").inner_text(timeout=10000)
                status = response.status if response else None
                title = page.title()
                host = urlsplit(page.url).hostname
                visible_official_text = (
                    "쿠팡 인플루언서" in body or
                    ("인플루언서" in body and "상품 링크" in body)
                )
                blocked = status in (401, 403, 429) or any(
                    marker in (title + "\n" + body).lower() for marker in DENIAL
                )
                result = {
                    "target": URL,
                    "status": status,
                    "final_host": host,
                    "title_length": len(title),
                    "visible_body_length": len(body),
                    "public_influencer_ui_visible": bool(
                        status == 200 and host == "influencers.coupang.com"
                        and visible_official_text and not blocked
                    ),
                    "product_link_text_visible": "상품 링크" in body,
                    "denial_detected": blocked,
                    "logged_in": False,
                    "login_attempted": False,
                    "link_generated": False,
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                if not result["public_influencer_ui_visible"]:
                    raise SystemExit(1)
            finally:
                context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
