"""Interactive local-only recovery of expired Kakao Developers Playwright login.

Opens a visible browser on the user's own PC for manual login, then streams
Kakao-scoped storage state directly to GitHub CLI encrypted Actions Secret.
Never print cookies, save a session file, commit auth data, or paste a token.
"""
from __future__ import annotations

import json
import subprocess
from urllib.parse import urlsplit

from gh_only.kakao_og_authenticated_probe import TOOL_URL, safe_session_state

REPO = "mw5114-tech/hotdeal-bot"
SECRET_NAME = "KAKAO_DEV_STORAGE_STATE_JSON"


def upload_secret(storage_state: dict, *, runner=subprocess.run) -> None:
    """Use stdin, not a shell argument or temporary file, for session tokens."""
    scoped = safe_session_state(json.dumps(storage_state))
    payload = json.dumps(scoped, ensure_ascii=False)
    result = runner(
        ["gh", "secret", "set", SECRET_NAME, "--repo", REPO],
        input=payload,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        # Github CLI stderr may contain sensitive data; never echo it.
        raise RuntimeError("GITHUB_CLI_SECRET_UPLOAD_FAILED")
    print("KAKAO_SESSION_SECRET_REFRESHED: login state uploaded; no cookies printed")


def main() -> None:
    from playwright.sync_api import sync_playwright

    print("Visible browser login required. No login tokens will be printed or saved.")
    print("Check that GitHub CLI (gh auth login) is already connected to your repo.")
    with sync_playwright() as play:
        # Use the locally installed Chrome, which can show normal login and 2FA.
        browser = play.chromium.launch(channel="chrome", headless=False)
        try:
            ctx = browser.new_context(locale="ko-KR")
            page = ctx.new_page()
            page.goto(TOOL_URL, wait_until="domcontentloaded", timeout=30000)
            input(
                "Sign in to Kakao Developers in the browser window, navigate "
                "back to the sharing debugger, then press Enter here: "
            )
            p = urlsplit(page.url)
            if p.scheme != "https" or p.hostname != "developers.kakao.com" or                     not p.path.startswith("/tool/debugger"):
                raise RuntimeError("KAKAO_LOGIN_NOT_VERIFIED")
            if not any(page.locator(sel).count() > 0 for sel in (
                'input[placeholder*="URL"]', 'input[type="url"]',
                'input[placeholder*="url"]',
            )):
                raise RuntimeError("KAKAO_TOOL_NOT_READY_AFTER_LOGIN")
            upload_secret(ctx.storage_state())
        finally:
            browser.close()


if __name__ == "__main__":
    main()
