"""Read-only Kakao Developers OG UI lookup with user-authorized web session.

Explicit opt-in only. Does NOT perform login, scrape other sites, reset cache,
send messages, publish posts or collect cookies. Never logs session data.
Only uses the documented web tool as a single, low-volume diagnostic.

This experimental tool is not an official OG REST API and a valid session
does not guarantee Kakao permits unattended use of its debugging interface.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TOOL_URL = "https://developers.kakao.com/tool/debugger/sharing"
SOURCE = "https://link.coupang.com/a/iiCmQxWdUG"
OUT = Path("kakao_authenticated_og_ui_report.json")
PROVIDER_ROOT_DOMAINS = ("kakao.com", "daum.net")
OG_KEYS = ("og:url", "og:title", "og:description", "og:image", "og:site_name")


def provider_domain_allowed(domain: str) -> bool:
    """Accept real Kakao/Daum subdomains, never unrelated or lookalike hosts."""
    if not isinstance(domain, str):
        return False
    host = domain.lstrip(".").lower()
    return any(host == root or host.endswith("." + root)
               for root in PROVIDER_ROOT_DOMAINS)


def safe_session_state(raw: str) -> dict:
    """Return provider-scoped state only; discard third-party cookies in memory.

    Never print, archive, or echo authentication values.
    """
    state = json.loads(raw)
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise ValueError("INVALID_AUTH_STATE")
    if any(not isinstance(cookie, dict) for cookie in state["cookies"]):
        raise ValueError("INVALID_COOKIE_RECORD")
    cookies = [cookie for cookie in state["cookies"]
               if provider_domain_allowed(cookie.get("domain"))]
    if not cookies:
        raise ValueError("NO_KAKAO_LOGIN_COOKIES")

    origins = state.get("origins", [])
    if not isinstance(origins, list):
        raise ValueError("INVALID_ORIGINS")
    scoped_origins = []
    for entry in origins:
        if not isinstance(entry, dict):
            continue
        url = entry.get("origin")
        if not isinstance(url, str):
            continue
        try:
            uri = urlsplit(url)
            if uri.scheme == "https" and provider_domain_allowed(uri.hostname):
                scoped_origins.append(entry)
        except ValueError:
            continue
    return {"cookies": cookies, "origins": scoped_origins}


def extract_og_table(page):
    """Read visible, labeled OG table cells. Never inspect raw session/header data."""
    data = {}
    for key in OG_KEYS:
        cell = page.get_by_text(key, exact=True)
        if not cell.count():
            continue
        try:
            text = cell.first.locator("xpath=..").inner_text(timeout=1200)
        except Exception:
            continue
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        value = "\n".join(x for x in lines if x != key).strip()
        if value:
            data[key] = value[:1600]
    return data


def image_is_cdn(url):
    try:
        parts = urlsplit(url.strip())
        return (parts.scheme == "https" and
                parts.hostname in ("thumbnail.coupangcdn.com", "image.coupangcdn.com") and
                parts.username is None and parts.password is None and
                parts.port in (None, 443) and
                parts.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
    except ValueError:
        return False


def run():
    report = {
        "schema": "github-kakao-authenticated-og-ui.v1",
        "source_url": SOURCE,
        "result": "NOT_RUN",
        "login_attempted": False,
        "account_data_logged": False,
        "submitted_requests": 0,
        "cache_reset": False,
        "messages_sent": 0,
        "posts_published": 0,
        "og_data": {},
        "cdn_image_url_candidate": None,
        "source_item_id_verified": False,
    }
    secret = os.environ.get("KAKAO_DEV_STORAGE_STATE_JSON", "")
    if not secret:
        report["result"] = "SESSION_SECRET_NOT_CONFIGURED"
    else:
        try:
            session = safe_session_state(secret)
            with sync_playwright() as play:
                browser = play.chromium.launch(headless=True)
                try:
                    ctx = browser.new_context(locale="ko-KR", storage_state=session)
                    page = ctx.new_page()
                    page.goto(TOOL_URL, wait_until="domcontentloaded", timeout=25000)
                    if urlsplit(page.url).hostname == "accounts.kakao.com" or "/login" in urlsplit(page.url).path:
                        report["result"] = "LOGIN_SESSION_EXPIRED_OR_REJECTED"
                    elif urlsplit(page.url).hostname != "developers.kakao.com":
                        report["result"] = "UNEXPECTED_PAGE"
                    else:
                        try:
                            page.wait_for_load_state("networkidle", timeout=9000)
                        except PlaywrightTimeout:
                            pass
                        selectors = ['input[placeholder*="URL"]',
                                     'input[aria-label*="URL"]', 'input[type="url"]',
                                     'input[placeholder*="url"]']
                        field = None
                        for selector in selectors:
                            q = page.locator(selector)
                            if q.count():
                                field = q.first
                                break
                        if field is None:
                            report["result"] = "URL_INPUT_NOT_FOUND"
                        else:
                            field.fill(SOURCE, timeout=6000)
                            trigger = page.get_by_role("button", name=re.compile("메타 정보 조회"))
                            if trigger.count() == 0:
                                trigger = page.get_by_text("메타 정보 조회", exact=True)
                            if trigger.count() == 0:
                                report["result"] = "LOOKUP_BUTTON_NOT_FOUND"
                            else:
                                trigger.first.click(timeout=6500)
                                report["submitted_requests"] = 1
                                try:
                                    page.get_by_text("og:image", exact=True).wait_for(timeout=18000)
                                except PlaywrightTimeout:
                                    pass
                                report["og_data"] = extract_og_table(page)
                                photo = report["og_data"].get("og:image")
                                if photo and image_is_cdn(photo):
                                    report["cdn_image_url_candidate"] = photo.strip()
                                    report["result"] = "OG_CDN_IMAGE_FOUND"
                                else:
                                    report["result"] = "NO_VALID_OG_IMAGE"
                    ctx.close()
                finally:
                    browser.close()
        except Exception as exc:
            # Type name only: no URL/query, auth session or response body in log.
            report["result"] = "EXPERIMENT_ERROR"
            report["error_type"] = type(exc).__name__
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    safe_print = {k: v for k, v in report.items()
                  if k not in ("og_data", "cdn_image_url_candidate")}
    safe_print["has_image_candidate"] = bool(report["cdn_image_url_candidate"])
    print("KAKAO_AUTHENTICATED_OG_PROBE " + json.dumps(safe_print, ensure_ascii=False))


if __name__ == "__main__":
    run()
