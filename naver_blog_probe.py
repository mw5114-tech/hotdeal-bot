"""Read-only Naver Blog connectivity and editor-session diagnostic.

This utility NEVER logs in, types content, saves drafts, or publishes posts.
For a persistent, already-authorized browser session, point
NAVER_BLOG_STORAGE_STATE_FILE at a local Playwright storage_state JSON file.
Do not commit that file or upload it to workflow artifacts.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def validate_blog_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", value or ""):
        raise ValueError("INVALID_BLOG_ID")
    return value


def editor_state(urls: list[str]) -> str:
    """Conservatively classify browser navigation, never treating unknown as success."""
    lowered = [str(url).lower() for url in urls]
    if any("nid.naver.com" in url or "nidlogin" in url for url in lowered):
        return "LOGIN_REQUIRED"
    if any("postwriteform.naver" in url or "postwrite" in url for url in lowered):
        return "EDITOR_FRAME_PRESENT"
    return "EDITOR_UNCONFIRMED"


def safe_site(url: str) -> str:
    """Do not log query strings, cookies, or authentication tokens."""
    parsed = urlsplit(url)
    return parsed.netloc or "unknown"


def run(mode: str, blog_id: str) -> int:
    from playwright.sync_api import sync_playwright

    blog_id = validate_blog_id(blog_id)
    homepage = f"https://blog.naver.com/{blog_id}"
    target = homepage if mode == "public" else f"{homepage}?Redirect=Write"
    storage = os.environ.get("NAVER_BLOG_STORAGE_STATE_FILE", "").strip()
    if mode == "editor" and (not storage or not Path(storage).is_file()):
        print("EDITOR_NOT_RUN: authenticated session file unavailable")
        return 3

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            options = {"storage_state": storage} if mode == "editor" else {}
            context = browser.new_context(**options)
            page = context.new_page()
            try:
                response = page.goto(target, wait_until="domcontentloaded", timeout=35000)
                page.wait_for_timeout(3000)
            except Exception as exc:
                # Print a stable code instead of exception strings, which may contain URLs.
                print(f"NAVIGATION_FAILED: type={type(exc).__name__}")
                return 2

            status = response.status if response else None
            domains = list(dict.fromkeys(safe_site(frame.url) for frame in page.frames))
            print(f"MODE={mode}; HTTP_STATUS={status}; PAGE_DOMAIN={safe_site(page.url)}")
            print(f"FRAME_DOMAINS={','.join(domains)}")
            if status is None or status >= 400:
                print("ACCESS_FAILED")
                return 2
            if mode == "public":
                print("PUBLIC_PAGE_HTTP_OK; this does not prove authenticated access")
                return 0

            state = editor_state([page.url] + [frame.url for frame in page.frames])
            print(f"EDITOR_CHECK={state}")
            if state == "EDITOR_FRAME_PRESENT":
                print("EDITOR_NAVIGATION_OK; no content was entered or published")
                return 0
            return 3
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("public", "editor"), default="public")
    parser.add_argument("--blog-id", default="mw5114")
    args = parser.parse_args()
    return run(args.mode, args.blog_id)


if __name__ == "__main__":
    raise SystemExit(main())
