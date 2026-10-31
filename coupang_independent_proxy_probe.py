"""Manual GitHub hosted runner egress verification with an authorized proxy.

No authentication, CAPTCHA, browser spoofing, hidden retries or publishing.
No credential or proxy endpoint is printed. A proxy service or user-controlled
remote egress must exist; this script cannot create a residential connection.
"""
from __future__ import annotations
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from urllib.request import urlopen

CHECK_URL = "https://checkip.amazonaws.com/"
PUBLIC_TARGET = "https://partners.coupang.com/"
BLOCK = re.compile(r"access\s*denied|forbidden|request\s*blocked|captcha|verify\s+you\s+are\s+human|접근[이\s]*차단", re.I)
BRAND = re.compile(r"쿠팡\s*파트너스|coupang\s*partners", re.I)


def proxy_config(server: str, username: str = "", password: str = "") -> dict:
    """Playwright accepts HTTP/HTTPS/SOCKS5; credentials are separate secrets."""
    if not server:
        raise ValueError("PROXY_NOT_CONFIGURED")
    if any(c in server for c in "\r\n\t ") or len(server) > 512:
        raise ValueError("PROXY_SERVER_INVALID")
    u = urlsplit(server)
    try:
        port = u.port
    except ValueError:
        raise ValueError("PROXY_SERVER_INVALID")
    if u.scheme not in {"http", "https", "socks5"} or not u.hostname or not port:
        raise ValueError("PROXY_SERVER_INVALID")
    if u.username or u.password or u.path not in {"", "/"} or u.query or u.fragment:
        raise ValueError("PROXY_SERVER_INVALID")
    if bool(username) != bool(password):
        raise ValueError("PROXY_CREDS_INCOMPLETE")
    if u.scheme == "socks5" and username:
        raise ValueError("SOCKS5_AUTH_UNSUPPORTED_BY_CHROMIUM")
    config = {"server": server}
    if username:
        config["username"] = username
        config["password"] = password
    return config


def read_ip(text: str) -> str | None:
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        return None


def direct_egress() -> str | None:
    try:
        with urlopen(CHECK_URL, timeout=10) as response:
            return read_ip(response.read(128).decode())
    except Exception:
        return None


def browser_probe(config: dict) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, proxy=config, timeout=30_000)
        try:
            context = browser.new_context(accept_downloads=False)
            try:
                page = context.new_page()
                response = page.goto(CHECK_URL, wait_until="domcontentloaded", timeout=20_000)
                proxy_ip = read_ip(page.locator("body").inner_text(timeout=4000))
                ip_http = response.status if response else None
                if not proxy_ip or ip_http != 200:
                    return {"proxy_confirmed": False, "reason": "PROXY_EGRESS_IP_UNVERIFIED"}
                return {"proxy_confirmed": True, "proxy_public_ip": proxy_ip}
            finally:
                context.close()
        finally:
            browser.close()


def target_probe(config: dict) -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, proxy=config, timeout=30_000)
        try:
            context = browser.new_context(accept_downloads=False)
            try:
                page = context.new_page()
                response = page.goto(PUBLIC_TARGET, wait_until="domcontentloaded", timeout=25_000)
                page.wait_for_timeout(600)
                status = response.status if response else None
                text = page.locator("body").inner_text(timeout=5000)[:20_000]
                title = page.title()[:140]
                host = urlsplit(page.url).hostname
                blocked = status in (401, 403, 429) or bool(BLOCK.search(title + "\n" + text))
                confirmed = bool(
                    isinstance(status, int) and 200 <= status < 300
                    and host == "partners.coupang.com"
                    and BRAND.search(title + "\n" + text)
                    and not blocked
                )
                return {
                    "status": status,
                    "host": host,
                    "classification": (
                        "PUBLIC_PARTNERS_UI_REACHED" if confirmed else
                        "DENIED_BY_SITE" if blocked else "PUBLIC_RESPONSE_UNCONFIRMED"
                    ),
                    "login_attempted": False,
                }
            finally:
                context.close()
        finally:
            browser.close()


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("GITHUB_RUNNER_REQUIRED")
        return 2
    try:
        config = proxy_config(
            os.getenv("EGRESS_PROXY_SERVER", ""),
            os.getenv("EGRESS_PROXY_USER", ""),
            os.getenv("EGRESS_PROXY_PASSWORD", ""),
        )
    except ValueError as exc:
        print(str(exc))
        return 2
    report = {"direct_ip": direct_egress(), "proxy": None, "public_page": None,
              "login_attempted": False, "uses_existing_external_proxy": True}
    try:
        report["proxy"] = browser_probe(config)
        if report["proxy"].get("proxy_confirmed"):
            proxy_ip = report["proxy"].get("proxy_public_ip")
            direct_ip = report["direct_ip"]
            report["egress_changed"] = bool(direct_ip and proxy_ip != direct_ip)
            if report["egress_changed"]:
                report["public_page"] = target_probe(config)
            else:
                report["public_page"] = {"classification": "NO_EGRESS_CHANGE"}
        else:
            report["public_page"] = {"classification": "PROXY_NOT_CONFIRMED"}
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["public_page"] = {"classification": "PROXY_OR_BROWSER_FAILURE"}
    Path("proxy-egress-observation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["public_page"].get("classification") == "PUBLIC_PARTNERS_UI_REACHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
