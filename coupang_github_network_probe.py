"""A bounded, unauthenticated GitHub-hosted runner access check for Coupang Partners.

Exactly one HTTP GET plus one real browser navigation per runner, with no retries,
login, stealth plugins, proxies, CAPTCHA interactions, screenshots or credentials.
Logs only bounded diagnostic categories, never page content or cookies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TARGET = "https://partners.coupang.com/"
BLOCKED_MARKERS = re.compile(
    r"access\s*denied|forbidden|request\s*blocked|접근[이\s]*차단|"
    r"로봇이\s*아닙니다|verify\s*you\s*are\s*human|captcha", re.I,
)
PARTNERS_MARKERS = re.compile(r"쿠팡\s*파트너스|coupang\s*partners", re.I)


def host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return "INVALID_URL"


def signals(text: str) -> dict:
    return {
        "deny_or_challenge_marker": bool(BLOCKED_MARKERS.search(text)),
        "partners_text_marker": bool(PARTNERS_MARKERS.search(text)),
    }


def dns_check() -> dict:
    try:
        infos = socket.getaddrinfo("partners.coupang.com", 443, type=socket.SOCK_STREAM)
        return {
            "dns_ok": bool(infos),
            "ipv4": any(item[0] == socket.AF_INET for item in infos),
            "ipv6": any(item[0] == socket.AF_INET6 for item in infos),
        }
    except (OSError, socket.error) as exc:
        return {"dns_ok": False, "exception": type(exc).__name__}


def http_check() -> dict:
    # urllib uses a normal Python client; do not forge identity or authorization.
    request = Request(TARGET, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            text = response.read(4096).decode("utf-8", errors="replace")
            return {
                "status": response.status, "final_host": host(response.url),
                "signals": signals(text), "result": "HTTP_RESPONSE",
            }
    except HTTPError as exc:
        # A 403 is a website response, not a network/connectivity failure.
        text = exc.read(4096).decode("utf-8", errors="replace")
        return {
            "status": exc.code, "final_host": host(exc.url),
            "signals": signals(text), "result": "HTTP_RESPONSE",
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": None, "result": "NETWORK_ERROR", "error_type": type(exc).__name__}


def browser_check(channel: str) -> dict:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as playwright:
            kwargs = {"headless": True, "timeout": 30000}
            if channel != "chromium":
                kwargs["channel"] = channel
            browser = playwright.chromium.launch(**kwargs)
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                nav_statuses = []

                def record_nav(response):
                    try:
                        if response.request.is_navigation_request() and response.frame == page.main_frame:
                            nav_statuses.append(response.status)
                    except Exception:
                        pass

                page.on("response", record_nav)
                try:
                    response = page.goto(TARGET, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(1500)
                    body = page.locator("body").inner_text(timeout=4000)[:4096]
                    title = page.title()
                    combined = (title + "\n" + body)
                    status = response.status if response else None
                    return {
                        "result": "BROWSER_RESPONSE",
                        "browser": browser.version,
                        "main_status": status,
                        "document_statuses": nav_statuses[:10],
                        "final_host": host(page.url),
                        "signals": signals(combined),
                        "redirected_to_login": bool(re.search("login|sign.?in|prelogin", page.url, re.I)),
                        "title_is_access_denied": bool(re.search("access denied|forbidden", title, re.I)),
                    }
                except Exception as exc:
                    return {
                        "result": "BROWSER_NAVIGATION_ERROR", "error_type": type(exc).__name__,
                        "network_error": (re.search(r"net::ERR_[A-Z_]+", str(exc)) or [None])[0],
                        "document_statuses": nav_statuses[:10],
                        "final_host": host(page.url),
                    }
            finally:
                browser.close()
    except Exception as exc:
        return {"result": "BROWSER_SETUP_ERROR", "error_type": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("chromium", "chrome"), default="chromium")
    parser.add_argument("--out", default="coupang-github-transport-report.json")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("This probe only runs on a GitHub Actions runner.")
        return 2
    output = {
        "probe": "COUPANG_GITHUB_ONLY_ONE_PASS",
        "runner_os": os.environ.get("RUNNER_OS"),
        "image_os": os.environ.get("ImageOS"),
        "browser_channel": args.channel,
        "dns": dns_check(),
        "python_http": http_check(),
        "browser_http": browser_check(args.channel),
        "authentication_attempted": False,
        "cookies_captured": False,
        "retry_count": 0,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
