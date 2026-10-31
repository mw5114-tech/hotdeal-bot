"""One-time Coupang Partners Chrome session exporter for the existing manual Actions probe.

Use on your own computer only. Writes an encrypted session envelope and key to
your home directory, never to Git. Neither file is sent anywhere automatically.

  python -m pip install playwright cryptography
  python coupang_session_export.py --mode cdp
  python coupang_session_export.py --mode managed
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA = "hotdeal.coupang.session.v1"
PAYLOAD_SCHEMA = "hotdeal.coupang.session.payload.v1"
DEFAULT_CDP = "http://127.0.0.1:9222"
DEFAULT_MEMBER = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
MAX_ENVELOPE_BYTES = 47000
ALLOWED_COOKIE_DOMAINS = {"partners.coupang.com", "coupang.com"}
DEFAULT_OUT = Path.home() / "coupang-session-transfer"


def validate_member_url(raw: str) -> str:
    if not isinstance(raw, str) or len(raw) > 2000:
        raise ValueError("MEMBER_URL_INVALID")
    url = urlsplit(raw)
    if (url.scheme != "https" or url.hostname != "partners.coupang.com"
            or url.port or url.username or url.password or url.query
            or (url.fragment and not re.fullmatch(r"/?[A-Za-z0-9/_-]+", url.fragment))
            or (url.path in ("", "/") and not url.fragment)
            or re.search(r"login|prelogin|sign.?in|/api/", url.path + "/" + url.fragment, re.I)):
        raise ValueError("DIRECT_MEMBER_URL_REQUIRED")
    return raw


def normalize_cookies(cookies: list[dict]) -> list[dict]:
    """Only domains accepted by the receiving workflow can leave this machine."""
    filtered = []
    seen = set()
    keys = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
    for cookie in cookies:
        domain = cookie.get("domain", "").lstrip(".").lower()
        if domain not in ALLOWED_COOKIE_DOMAINS:
            continue
        if "partitionKey" in cookie:
            raise ValueError("PARTITIONED_STATE_UNSUPPORTED")
        if not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str):
            raise ValueError("COOKIE_SHAPE_INVALID")
        result = {key: cookie[key] for key in keys if key in cookie}
        if not all(key in result for key in ("name", "value", "domain", "path")):
            raise ValueError("COOKIE_SHAPE_INVALID")
        marker = (result["name"], result["domain"], result["path"])
        if marker not in seen:
            seen.add(marker)
            filtered.append(result)
    if not filtered:
        raise ValueError("NO_COUPANG_COOKIES")
    return filtered


def make_payload(member_url: str, cookies: list[dict], local_storage: dict[str, str]) -> dict:
    member_url = validate_member_url(member_url)
    normalized = normalize_cookies(cookies)
    if not isinstance(local_storage, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in local_storage.items()
    ):
        raise ValueError("STORAGE_SHAPE_INVALID")
    return {
        "schema": PAYLOAD_SCHEMA,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "memberUrl": member_url,
        "state": {
            "cookies": normalized,
            "origins": [{"origin": "https://partners.coupang.com",
                         "localStorage": [{"name": k, "value": v} for k, v in local_storage.items()]}],
        },
    }


def encrypt_payload(payload: dict) -> tuple[str, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(), SCHEMA.encode())
    envelope = json.dumps({"schema": SCHEMA, "cipher": "AES-256-GCM",
                           "iv": base64.b64encode(nonce).decode(),
                           "ciphertext": base64.b64encode(encrypted).decode()}, separators=(",", ":"))
    if len(envelope.encode()) > MAX_ENVELOPE_BYTES:
        raise ValueError("STATE_TOO_LARGE_FOR_GITHUB_SECRET")
    return envelope, base64.b64encode(key).decode()


def save_secret(path: Path, value: str) -> None:
    if path.exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)


def find_member_page(context):
    # Existing logged-in tab only. Never scrape or export other sites.
    for page in context.pages:
        try:
            validate_member_url(page.url)
            return page
        except ValueError:
            continue
    return None


def capture(context) -> tuple[str, list[dict], dict[str, str]]:
    page = find_member_page(context)
    if page is None:
        raise ValueError("OPEN_QUICK_LINK_PAGE_FIRST")
    member_url = validate_member_url(page.url)
    cookies = context.cookies(["https://partners.coupang.com/", "https://coupang.com/"])
    storage = page.evaluate("""() => Object.fromEntries(
        Array.from({length: localStorage.length}, (_, i) => localStorage.key(i))
          .map(k => [k, localStorage.getItem(k)]))""")
    return member_url, cookies, storage


def export_from_cdp(endpoint: str):
    from playwright.sync_api import sync_playwright
    if endpoint != DEFAULT_CDP:
        raise ValueError("LOCAL_CDP_ONLY")
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=10000)
        if not browser.contexts:
            raise ValueError("CHROME_CONTEXT_NOT_FOUND")
        # Do not close the user's Chrome; the CDP transport disconnects on exit.
        return capture(browser.contexts[0])


def export_from_managed(profile: Path):
    from playwright.sync_api import sync_playwright
    profile = profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile), channel="chrome", headless=False, viewport={"width": 1280, "height": 880}
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(DEFAULT_MEMBER, wait_until="domcontentloaded", timeout=30000)
            print("크롬 창에서 쿠팡 파트너스에 직접 로그인하고 '간편 링크 만들기' 화면을 여셈.")
            input("로그인 화면 확인 후 Enter: ")
            return capture(context)
        finally:
            context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervised one-time Coupang session export")
    parser.add_argument("--mode", choices=("cdp", "managed"), default="cdp")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP)
    parser.add_argument("--profile", type=Path, default=DEFAULT_OUT / "managed-chrome")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT / "secrets")
    args = parser.parse_args()
    try:
        if args.mode == "cdp":
            print("현재 열린 Chrome의 원격 디버깅 포트를 사용함.")
            member_url, cookies, storage = export_from_cdp(args.cdp_url)
        else:
            member_url, cookies, storage = export_from_managed(args.profile)
        envelope, key = encrypt_payload(make_payload(member_url, cookies, storage))
        args.out.mkdir(parents=True, exist_ok=True)
        first = args.out / "COUPANG_SESSION_ENVELOPE.txt"
        second = args.out / "COUPANG_SESSION_KEY.txt"
        if first.exists() or second.exists():
            raise ValueError("OUTPUT_ALREADY_EXISTS")
        save_secret(first, envelope)
        save_secret(second, key)
        print("EXPORT_OK. 파일 내용을 같은 이름의 GitHub Actions Secrets에 각각 등록:")
        print(first)
        print(second)
        print("Actions > Coupang session reuse > Run workflow > mode=session 으로 확인.")
        print("GitHub 403 차단과 실제 제휴 링크 자동 생성 성공 여부는 별도 검증 필요.")
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]+", str(exc)) else "BROWSER_OR_EXPORT_FAILED"
        print("EXPORT_FAILED:", code)
        if args.mode == "cdp":
            print("Chrome 136+ 기본 프로필은 CDP 접속이 제한될 수 있음. --mode managed 로 한 번 로그인해 시도.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
