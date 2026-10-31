"""Site-owner controlled Coupang Partners WAF acceptance test from GitHub Actions.

Only checks the public homepage once per transport. Does not log in, scrape,
automate affiliate links, spoof IPs/browser signatures, or bypass a WAF.
Site owner can explicitly authorize a test request by configuring an upstream
rule for an exact host/path and an owner-chosen shared header token.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TARGET = "https://partners.coupang.com/"
HEADER_NAME = "X-Coupang-Owner-Test"
DENIED = re.compile(r"access\s*denied|forbidden|request\s*blocked|접근[이\s]*차단|captcha|verify\s+you\s+are\s+human", re.I)
PARTNERS = re.compile(r"쿠팡\s*파트너스|coupang\s*partners", re.I)
MAX_READ = 20000


def validate_owner_token(token: str) -> str:
    if not token:
        return ""
    if len(token) < 24 or len(token) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("INVALID_OWNER_TEST_TOKEN_FORMAT")
    return token


def classify(status: int | None, host: str, title: str, body: str) -> str:
    if status in (401, 403, 429) or DENIED.search(title + "\n" + body):
        return "DENIED_BY_SITE"
    if status is None:
        return "NO_HTTP_RESPONSE"
    if not (200 <= status < 300 and host == "partners.coupang.com"):
        return "HTTP_OR_DESTINATION_UNEXPECTED"
    if PARTNERS.search(title + "\n" + body):
        return "PUBLIC_PARTNERS_UI_REACHED"
    return "PUBLIC_RESPONSE_UNCONFIRMED"


def safe_headers(headers) -> dict[str, str]:
    # Diagnostic values, not any Set-Cookie, Authorization or arbitrary headers.
    keep = {"server", "cf-ray", "x-request-id", "x-akamai-request-id", "x-cache", "content-type"}
    return {
        str(k).lower(): str(v)[:130]
        for k, v in headers.items()
        if str(k).lower() in keep
    }


def external_ip() -> dict:
    try:
        with urlopen("https://checkip.amazonaws.com", timeout=12) as response:
            raw = response.read(100).decode().strip()
            ip = ipaddress.ip_address(raw)
            return {"ip": str(ip), "kind": "ipv6" if ip.version == 6 else "ipv4"}
    except Exception as exc:
        return {"ip": None, "error_type": type(exc).__name__}


def request_public_page(token: str) -> dict:
    headers = {HEADER_NAME: token} if token else {}
    req = Request(TARGET, headers=headers, method="GET")
    try:
        response = urlopen(req, timeout=22)
    except HTTPError as err:
        response = err
    except (URLError, OSError) as exc:
        return {"status": None, "classification": "NO_HTTP_RESPONSE",
                "error_type": type(exc).__name__}
    with response:
        body = response.read(MAX_READ).decode("utf-8", errors="replace")
        status = response.status if hasattr(response, "status") else response.code
        host = urlsplit(response.url).hostname or ""
        return {"status": status, "host": host,
                "classification": classify(status, host, "", body),
                "response_headers": safe_headers(response.headers)}


def browser_public_page(token: str) -> dict:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=30000)
            try:
                ctx = browser.new_context(extra_http_headers={HEADER_NAME: token} if token else {},
                                          accept_downloads=False)
                page = ctx.new_page()
                try:
                    response = page.goto(TARGET, wait_until="domcontentloaded", timeout=28000)
                    page.wait_for_timeout(1200)
                    title = page.title()[:160]
                    body = page.locator("body").inner_text(timeout=5000)[:MAX_READ]
                    status = response.status if response else None
                    host = urlsplit(page.url).hostname or ""
                    result = {
                        "status": status, "host": host,
                        "classification": classify(status, host, title, body),
                        "response_headers": safe_headers(response.headers) if response else {},
                        "is_access_denied_title": bool(DENIED.search(title)),
                        "login_attempted": False,
                    }
                    return result
                except Exception as exc:
                    return {"status": None, "classification": "BROWSER_NAVIGATION_FAILED",
                            "error_type": type(exc).__name__}
                finally:
                    ctx.close()
            finally:
                browser.close()
    except Exception as exc:
        return {"status": None, "classification": "BROWSER_SETUP_FAILED",
                "error_type": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("owner-waf-probe.json"))
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("GITHUB_ACTIONS_REQUIRED")
        return 2
    try:
        token = validate_owner_token(os.environ.get("COUPANG_WAF_TEST_TOKEN", ""))
    except ValueError:
        print("INVALID_OWNER_TEST_TOKEN_FORMAT")
        return 2
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "target": "partners.coupang.com",
        "runner": os.environ.get("RUNNER_OS"),
        "egress": external_ip(),
        "owner_rule_token_present": bool(token),
        "python_https": request_public_page(token),
        "chromium": browser_public_page(token),
        "login_attempted": False,
        "attempts_per_transport": 1,
    }
    success = report["chromium"]["classification"] == "PUBLIC_PARTNERS_UI_REACHED"
    report["site_access_confirmed"] = success
    print(json.dumps(report, ensure_ascii=False, indent=2))
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
