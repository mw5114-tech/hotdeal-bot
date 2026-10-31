"""One-shot whikare item integration: Jirum text + Kakao OG photo + Lowchart history.

Explicit user-requested trial for one concrete shortlink. No publishing, no
affiliate-link creation, no login attempt, no cache resets and no Coupang product
HTML requests. Kakao developer session is supplied only from an Actions secret.
Do not log, archive, screenshot, or expose any session credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib import request, error
from urllib.parse import parse_qs, urljoin, urlsplit
import json
import os
import re

from gh_only.kakao_og_authenticated_probe import (
    TOOL_URL, safe_session_state, extract_og_table, image_is_cdn
)
from gh_only.lowchart_price_reference import fetch_one

PRODUCT_ID = "9217210892"
ITEM_ID = "27232840915"
VENDOR_ITEM_ID = "94199974724"
SOURCE_SHORT = "https://link.coupang.com/a/iiPBYpVHae"
SOURCE_PRODUCT = "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724"
TITLE = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개"
PRICE_NOW = 12050
ORIGINAL_STATED = 30000
STATED_SAVING = 17950
STATED_PERCENT = 59.8
OUTPUT = Path("whikare-test-output")
OUTPUT.mkdir(exist_ok=True)
ROOTS = ("thumbnail.coupangcdn.com", "image.coupangcdn.com")


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def shortlink_302():
    """Inspect one redirect only. Do not enter the protected product page."""
    req = request.Request(SOURCE_SHORT, headers={"Accept": "text/html"},
                          method="GET")
    opener = request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=18) as response:
            code = response.status
            location = response.getheader("Location")
    except error.HTTPError as exc:
        code, location = exc.code, exc.headers.get("Location")
    except Exception as exc:
        return {"result": "NETWORK_ERROR", "error_type": type(exc).__name__}
    if code not in (301, 302, 303, 307, 308) or not location:
        return {"result": "NO_REDIRECT", "http_status": code}
    u = urlsplit(urljoin(SOURCE_SHORT, location))
    p = parse_qs(u.query)
    pid = re.search(r"^/vp/products/(\d+)(?:/)?$", u.path)
    has_vendor = "vendorItemId" in p
    return {
        "result": "REDIRECT_INSPECTED",
        "http_status": code,
        "destination_host": u.hostname,
        "product_id": pid.group(1) if pid else None,
        "item_id": p.get("itemId", [None])[0],
        "vendor_item_id": p.get("vendorItemId", [None])[0] if has_vendor else None,
        "same_product": bool(pid and pid.group(1) == PRODUCT_ID),
        "same_item": p.get("itemId", [None])[0] == ITEM_ID,
    }


def kakao_one_lookup():
    state = os.environ.get("KAKAO_DEV_STORAGE_STATE_JSON", "")
    response = {
        "status": "NOT_RUN",
        "source": SOURCE_SHORT,
        "submitted_queries": 0,
        "og_title": None,
        "og_url": None,
        "og_description": None,
        "og_image": None,
        "image_downloaded": False,
        "image_file": None,
        "image_bytes": 0,
        "og_product_id_matches": False,
        "exact_item_image_verified": False,
    }
    if not state:
        response["status"] = "SESSION_SECRET_MISSING"
        return response
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        session = safe_session_state(state)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(storage_state=session, locale="ko-KR")
                page = ctx.new_page()
                page.goto(TOOL_URL, wait_until="domcontentloaded", timeout=25000)
                if urlsplit(page.url).hostname == "accounts.kakao.com" or "/login" in urlsplit(page.url).path:
                    response["status"] = "LOGIN_SESSION_EXPIRED"
                    return response
                if urlsplit(page.url).hostname != "developers.kakao.com":
                    response["status"] = "UNEXPECTED_PAGE"
                    return response
                try:
                    page.wait_for_load_state("networkidle", timeout=9000)
                except PWTimeout:
                    pass
                field = None
                for selector in ('input[placeholder*="URL"]', 'input[aria-label*="URL"]',
                                 'input[type="url"]', 'input[placeholder*="url"]'):
                    option = page.locator(selector)
                    if option.count() and option.first.is_visible():
                        field = option.first
                        break
                if field is None:
                    response["status"] = "URL_INPUT_NOT_FOUND"
                    return response
                field.fill(SOURCE_SHORT, timeout=6000)
                button = page.get_by_role("button", name=re.compile("메타 정보 조회"))
                if not button.count():
                    button = page.get_by_text("메타 정보 조회", exact=True)
                if not button.count():
                    response["status"] = "SUBMIT_BUTTON_NOT_FOUND"
                    return response
                button.first.click(timeout=6500)
                response["submitted_queries"] = 1
                try:
                    page.get_by_text("og:image", exact=True).wait_for(timeout=18000)
                except PWTimeout:
                    pass
                metadata = extract_og_table(page)
                response["og_title"] = metadata.get("og:title")
                response["og_url"] = metadata.get("og:url")
                response["og_description"] = metadata.get("og:description")
                image_url = metadata.get("og:image")
                response["og_image"] = image_url
                og_url = response["og_url"] or ""
                parts = urlsplit(og_url)
                response["og_product_id_matches"] = (parts.hostname in ("www.coupang.com", "coupang.com")
                    and parts.path.rstrip("/") == "/vp/products/" + PRODUCT_ID)
                if not image_url or not image_is_cdn(image_url):
                    response["status"] = "OG_NO_TRUSTED_IMAGE"
                    return response
                # Image is not vendor-item proven: og:url generally omits itemId.
                response["status"] = "OG_IMAGE_URL_FOUND"
                return response
            finally:
                browser.close()
    except Exception as exc:
        response["status"] = "KAKAO_ERROR"
        response["error_class"] = type(exc).__name__
        return response


def download_verified_cdn(og: dict):
    url = og.get("og_image")
    if not url or not image_is_cdn(url) or not og.get("og_product_id_matches"):
        return
    parts = urlsplit(url)
    if parts.hostname not in ROOTS:
        return
    # Limit to static JPG/PNG/WebP bytes; no redirects to other hosts.
    req = request.Request(url, headers={"Accept": "image/*"})
    opener = request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=25) as response:
            mime = response.headers.get_content_type()
            data = response.read(2_500_001)
            code = response.status
        suffix = (".jpg" if mime == "image/jpeg"
                  else ".png" if mime == "image/png"
                  else ".webp" if mime == "image/webp" else None)
        signature_ok = ((suffix == ".jpg" and data[:3] == b"\xff\xd8\xff")
                        or (suffix == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n")
                        or (suffix == ".webp" and data[:4] == b"RIFF"
                            and data[8:12] == b"WEBP"))
        if code == 200 and suffix and signature_ok and 1000 <= len(data) <= 2_500_000:
            name = "whikare_kakao_og_product" + suffix
            (OUTPUT / name).write_bytes(data)
            og["image_downloaded"] = True
            og["image_file"] = name
            og["image_bytes"] = len(data)
        else:
            og["image_download_error"] = "IMAGE_SIGNATURE_OR_SIZE_INVALID"
    except Exception as exc:
        og["image_download_error"] = type(exc).__name__


def main():
    special_text = {
        "origin": "user_supplied_jirum_screenshot",
        "title": TITLE,
        "deal_price_krw": PRICE_NOW,
        "advertised_normal_price_krw": ORIGINAL_STATED,
        "advertised_saving_krw": STATED_SAVING,
        "advertised_discount_percent": STATED_PERCENT,
        "arithmetic_saving_matches": ORIGINAL_STATED - PRICE_NOW == STATED_SAVING,
        "computed_discount_percent": round(100 * STATED_SAVING / ORIGINAL_STATED, 1),
        "price_source": "telegram_post_as_sent_not_live_checkout",
        "shortlink": SOURCE_SHORT,
        "product_id": PRODUCT_ID,
        "item_id": ITEM_ID,
        "vendor_item_id": VENDOR_ITEM_ID,
    }

    redirect = shortlink_302()
    history = fetch_one(PRODUCT_ID, ITEM_ID,
                        ("휘카레", "230g", "20개"), PRICE_NOW)
    og = kakao_one_lookup()
    download_verified_cdn(og)

    # Fail closed on contradictory product identifiers or option titles:
    # do not attach historical values or the image to another product.
    if redirect.get("result") == "REDIRECT_INSPECTED":
        redirect_matches = redirect.get("same_product") and redirect.get("same_item")
    else:
        redirect_matches = False
    history_usable = bool(history.get("usable_historical_reference"))
    image_usable = bool(og.get("image_downloaded") and og.get("og_product_id_matches"))
    report = {
        "schema": "gh-only-whikare-exact-item-test.v1",
        "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        "telegram": special_text,
        "link_one_hop": redirect,
        "lowchart": history,
        "kakao": og,
        "matched_original_item_redirect": bool(redirect_matches),
        "history_available_for_target_option": history_usable,
        "image_available_for_target_product": image_usable,
        "photo_exact_vendor_item_verified": False,
        "old_price_officially_verified": False,
        "posts_published": 0, "affiliate_links_created": 0,
    }
    (OUTPUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [
        TITLE,
        f"뽐질 표기 특가: {PRICE_NOW:,}원",
        f"뽐질 표기 정상가: {ORIGINAL_STATED:,}원 (쿠팡 공식 정가 검증 X)",
        f"뽐질 표기 할인액: {STATED_SAVING:,}원 ({STATED_PERCENT:.1f}%)",
        f"상품번호: {PRODUCT_ID}; itemId: {ITEM_ID}; vendorItemId: {VENDOR_ITEM_ID}",
        f"뽐질 단축링크 원홉: {redirect.get('result')}, 정확한 itemId 일치={redirect.get('same_item')}",
        f"로우차트: {history.get('reason')}, 평균가={history.get('observed_average_krw')}, 최저={history.get('observed_low_krw')}, 최고={history.get('observed_high_krw')}",
        f"카카오 OG: {og.get('status')}, og:title={og.get('og_title')}, 상품번호 일치={og.get('og_product_id_matches')}",
        f"이미지 저장: {og.get('image_file') or '실패'} (해당 vendorItemId 이미지 일치 검증 X)",
        "외부 가격 이력은 실제 정상가나 특정 날짜의 정가가 아님; 게시 없음.",
    ]
    (OUTPUT / "readme.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    # Log compact status only. Never print sensitive cookie values or full third-party HTML.
    safe = {
        "telegram_title": TITLE, "deal_price_krw": PRICE_NOW,
        "advertised_normal_price_krw": ORIGINAL_STATED,
        "short_redirect": redirect, "lowchart": {
            "reason": history.get("reason"),
            "http_status": history.get("http_status"),
            "page_title": history.get("page_title"),
            "average": history.get("observed_average_krw"),
            "low": history.get("observed_low_krw"),
            "high": history.get("observed_high_krw"),
            "date": history.get("source_last_change_date"),
        },
        "kakao": {
            "status": og.get("status"), "og_title": og.get("og_title"),
            "product_id_match": og.get("og_product_id_matches"),
            "photo_saved": og.get("image_downloaded"),
            "image_file": og.get("image_file"),
            "image_bytes": og.get("image_bytes"),
        },
    }
    print("WHIKARE_GITHUB_TEST " + json.dumps(safe, ensure_ascii=False))


if __name__ == "__main__":
    main()
