"""One approved-link UI worker for a *trusted, always-on* GitHub self-hosted runner.

This never copies the user's default Chrome profile. A separate persistent
Chrome profile must be logged in by the owner on the execution machine first.
GitHub-hosted runners have produced HTTP 403 even with restored auth state.
No proxies, browser stealth, CAPTCHA handling, credential extraction, or
unsupported private API calls. An uncertain click is never retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

from gh_only.partner_api import canonical_product_url
from gh_only.shortlink_probe import one_hop

PARTNERS_URL = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
SHORT_URL = re.compile(r"https://(?:link\.coupang\.com/a/|coupa\.ng/)[A-Za-z0-9_-]+")
OUT = Path("partners_worker_result.json")


def canonical_identity(product_url: str) -> tuple[str, str, str | None]:
    cleaned = canonical_product_url(product_url)
    p = urlsplit(cleaned)
    ids = parse_qs(p.query)
    return p.path.split("/")[-1], ids["itemId"][0], ids.get("vendorItemId", [None])[0]


def matching_result_link(link: str, canonical: str, resolver=one_hop) -> bool:
    """An affiliate short URL must resolve to the exact requested option."""
    try:
        from gh_only.partner_api import safe_link
        if not safe_link(link):
            return False
        hop = resolver(link)
        if hop.get("result") != "DIRECT_PRODUCT_ID_CANDIDATE":
            return False
        product_id, item_id, vendor = canonical_identity(canonical)
        return (
            hop.get("product_id") == product_id
            and hop.get("item_id") == item_id
            and (vendor is None or hop.get("vendor_item_id") == vendor)
        )
    except Exception:
        return False


def visible_links(page) -> set[str]:
    # Only known link shapes from visible UI; never session values.
    text = page.evaluate("""() => {
        const items = [...document.querySelectorAll('input,textarea,a[href]')];
        const values = items.filter(x => x.getClientRects().length > 0)
          .flatMap(x => [x.value || '', x.href || '', x.textContent || '']);
        values.push(document.body?.innerText || '');
        return values.join('\\n');
    }""")
    return set(SHORT_URL.findall(text or ""))


def check_form(page) -> tuple:
    if urlsplit(page.url).hostname != "partners.coupang.com":
        raise RuntimeError("NOT_ON_PARTNERS_PAGE")
    input_el = page.locator("input#url")
    if input_el.count() != 1 or not input_el.is_visible():
        raise RuntimeError("NEEDS_LOGIN_OR_FORM_CHANGED")
    button = page.get_by_role("button", name=re.compile(r"링크\s*생성"))
    if button.count() != 1 or not button.is_visible() or not button.is_enabled():
        raise RuntimeError("GENERATE_BUTTON_UNCONFIRMED")
    return input_el, button


def create_once(page, product_url: str, resolver=one_hop, sleeper=time.sleep) -> dict:
    """Exactly one UI click, only after matching exact form; fail closed."""
    cleaned = canonical_product_url(product_url)
    input_el, button = check_form(page)
    input_el.fill(cleaned, timeout=6000)
    if input_el.input_value() != cleaned:
        return {"status": "INPUT_NOT_CONFIRMED", "clicks": 0}
    before = visible_links(page)
    button.click(timeout=7000)
    # A clicked form could have succeeded even if response/UI is delayed.
    # Never click again; if result cannot be proved, return uncertainty.
    for _ in range(12):
        sleeper(1)
        after = visible_links(page)
        found = sorted(after - before)
        if len(found) > 1:
            return {"status": "MULTIPLE_NEW_LINKS", "clicks": 1}
        if len(found) == 1:
            if matching_result_link(found[0], cleaned, resolver):
                return {
                    "status": "NEW_LINK_EXACT_OPTION_VERIFIED",
                    "clicks": 1,
                    "generated_link": found[0],
                    "canonical_product_url": cleaned,
                    "account_owner_independently_verified": False,
                }
            return {"status": "NEW_LINK_ITEM_NOT_VERIFIED", "clicks": 1}
    return {"status": "LINK_RESULT_UNCERTAIN_NO_RETRY", "clicks": 1}


def run_live(mode: str, product_url: str | None, profile_dir: Path, *, playwright_factory=None) -> dict:
    if not profile_dir.is_absolute():
        raise ValueError("ABSOLUTE_PROFILE_DIRECTORY_REQUIRED")
    if not profile_dir.exists() or not profile_dir.is_dir():
        raise ValueError("DEDICATED_PROFILE_NOT_INITIALIZED")
    if mode == "generate-one" and os.getenv("GH_ONLY_PARTNERS_GENERATE_ENABLED") != "true":
        raise ValueError("EXPLICIT_GENERATE_OPT_IN_REQUIRED")
    from playwright.sync_api import sync_playwright
    factory = playwright_factory or sync_playwright
    with factory() as p:
        # Using Chrome on a stable, trusted host, with a dedicated local
        # browser profile. Never use the OS default Chrome profile.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), channel="chrome", headless=False,
            locale="ko-KR", accept_downloads=False,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(PARTNERS_URL, wait_until="domcontentloaded", timeout=25000)
            if response is not None and response.status == 403:
                return {"status": "ACCESS_DENIED_STOP", "http_status": 403, "clicks": 0}
            try:
                _, _ = check_form(page)
            except RuntimeError as exc:
                return {"status": str(exc), "clicks": 0}
            if mode == "read-only":
                return {"status": "SIGNED_IN_QUICK_LINK_FORM_VISIBLE", "clicks": 0}
            return create_once(page, product_url or "")
        finally:
            context.close()


def setup_profile(profile_dir: Path) -> None:
    if not profile_dir.is_absolute():
        raise ValueError("ABSOLUTE_PROFILE_DIRECTORY_REQUIRED")
    if os.getenv("GITHUB_ACTIONS") == "true":
        raise ValueError("INITIAL_LOGIN_MUST_BE_INTERACTIVE_ON_TRUSTED_HOST")
    profile_dir.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir), channel="chrome", headless=False,
            locale="ko-KR", accept_downloads=False
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(PARTNERS_URL, wait_until="domcontentloaded", timeout=25000)
            if response is not None and response.status == 403:
                print("ACCESS_DENIED_STOP: This host cannot use the website.")
                return
            print("Use this normal Chrome window to sign in yourself if allowed.")
            input("After seeing the official quick-link page, press Enter here: ")
            try:
                check_form(page)
                print("SIGNED_IN_QUICK_LINK_FORM_VISIBLE; profile kept on this host.")
            except RuntimeError:
                print("FORM_NOT_CONFIRMED; do not activate generation.")
        finally:
            context.close()


def validate_request_packet(packet: dict) -> tuple[str, str]:
    """Reject stale, edited, or unverified GitHub-generated requests."""
    if not isinstance(packet, dict) or packet.get("schema") != "jirum-partners-one-link-request-v1":
        raise ValueError("INVALID_REQUEST_PACKET")
    if (packet.get("status") != "READY_FOR_AUTHENTICATED_PARTNERS_UI" or
            packet.get("personal_affiliate_link_generated") is not False or
            packet.get("publish_count") != 0):
        raise ValueError("REQUEST_NOT_READY")
    mid = packet.get("source_message_id")
    if not isinstance(mid, int) or isinstance(mid, bool) or mid < 1:
        raise ValueError("INVALID_MESSAGE_ID")
    url = canonical_product_url(packet.get("canonical_product_url", ""))
    expected = hashlib.sha256((str(mid) + "\n" + url).encode()).hexdigest()[:20]
    if packet.get("request_id") != expected:
        raise ValueError("REQUEST_ID_MISMATCH")
    return url, expected


def cli() -> int:
    parser = argparse.ArgumentParser(description="Trusted GitHub self-hosted Partners UI worker")
    parser.add_argument("--mode", choices=("fixture", "setup-profile", "read-only", "generate-one"),
                        default="fixture")
    parser.add_argument("--product-url")
    parser.add_argument("--request-file")
    parser.add_argument("--profile-dir", default=os.getenv("PARTNERS_PROFILE_DIR", ""))
    args = parser.parse_args()
    output: dict = {"status": "NOT_RUN", "clicks": 0, "auto_posted": False}
    try:
        requested_url = args.product_url
        request_id = None
        if args.request_file:
            packet = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
            requested_url, request_id = validate_request_packet(packet)
            if args.product_url and canonical_product_url(args.product_url) != requested_url:
                raise ValueError("REQUEST_URL_MISMATCH")
        if args.mode == "fixture":
            canonical = canonical_product_url(requested_url or
                "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724")
            output = {"status": "FIXTURE_ONLY_NO_NETWORK", "clicks": 0,
                      "auto_posted": False, "canonical_product_url": canonical}
        else:
            if not args.profile_dir:
                raise ValueError("PARTNERS_PROFILE_DIR_REQUIRED")
            profile = Path(args.profile_dir).expanduser().resolve()
            if args.mode == "setup-profile":
                setup_profile(profile)
                output = {"status": "MANUAL_SETUP_FINISHED", "clicks": 0, "auto_posted": False}
            else:
                if args.mode == "generate-one" and not requested_url:
                    raise ValueError("PRODUCT_URL_REQUIRED")
                output = {**output, **run_live(args.mode, requested_url, profile)}
        if request_id:
            output["request_id"] = request_id
    except Exception as exc:
        # Never print exceptions that could include cookies, account details or
        # browser profile contents. Only safe known failure reason types.
        error = str(exc)
        if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z0-9_]+", error):
            output["status"] = error
        else:
            output["status"] = "WORKER_ERROR"
            output["error_class"] = type(exc).__name__
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The link is emitted only after verification and stored in a private
    # GitHub Actions artifact; no cookies or account identifiers are included.
    print("PARTNERS_WORKER_RESULT " + json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] in (
        "FIXTURE_ONLY_NO_NETWORK", "SIGNED_IN_QUICK_LINK_FORM_VISIBLE",
        "NEW_LINK_EXACT_OPTION_VERIFIED", "MANUAL_SETUP_FINISHED"
    ) else 1


if __name__ == "__main__":
    sys.exit(cli())
