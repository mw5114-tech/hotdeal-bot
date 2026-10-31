"""One-time local setup. No existing browser profiles are read.
Run from this private checkout after: gh auth login
Requires playwright and installed Chrome. User signs in directly in the new window.
"""
import json
import re
import subprocess
import sys

REPO = "mw5114-tech/hotdeal-bot"
SECRET = "COUPANG_PARTNERS_STORAGE_STATE_JSON"
URL = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"


def main():
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode:
        print("GitHub CLI login required. Run gh auth login, then rerun this helper.")
        return 1
    from playwright.sync_api import sync_playwright
    from gh_only.partners_authenticated_access_probe import safe_coupang_state
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(URL, wait_until="domcontentloaded")
            print("Sign in yourself in the new Chrome window, then open Quick Link.")
            print("After the form is verified, this helper sends only Coupang session state")
            print("to the COUPANG_PARTNERS_STORAGE_STATE_JSON secret in mw5114-tech/hotdeal-bot.")
            input("Press Enter here after login and opening Quick Link: ")
            if page.url.rstrip("/") != URL:
                print("Quick Link route not open. No secret uploaded.")
                return 1
            if not (page.locator("input#url").is_visible() and
                    page.get_by_role("button", name=re.compile(r"^링크\s*생성[\s\u200b]*$")).is_visible()):
                print("Quick Link form not confirmed. No secret uploaded.")
                return 1
            state = safe_coupang_state(json.dumps(context.storage_state()))
            raw = json.dumps(state).encode()
            if len(raw) > 47000:
                print("State exceeds secret size budget. No secret uploaded.")
                return 1
            result = subprocess.run(
                ["gh", "secret", "set", SECRET, "--repo", REPO],
                input=raw, capture_output=True,
            )
            del state, raw
            if result.returncode:
                print("GitHub Secret upload failed. Details suppressed to protect session data.")
                return 1
            print("GitHub Secret saved. No session file or cookie values were printed.")
            print("Run Partners session UI regression and opt-in restore via workflow_dispatch.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("Setup failed: " + type(exc).__name__ + ". Session details suppressed.")
        sys.exit(1)
