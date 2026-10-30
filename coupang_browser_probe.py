"""One-product, supervised Coupang Partners quick-link generation probe.

Local Windows/desktop only. NO Telegram, GitHub state mutation or bulk
automation. Login in the visible Playwright Chromium window yourself.

Run:
  python -m pip install playwright
  python -m playwright install chromium
  python coupang_browser_probe.py --source-url "https://link.coupang.com/a/exampleCode"
  # or: --product-url "https://www.coupang.com/vp/products/123456789?itemId=123&vendorItemId=456"

Do not run this in CI: account login and user confirmation are required.
"""
import argparse
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, parse_qs, urlencode

PARTNERS_URL = "https://partners.coupang.com/"
PROFILE_DIR = Path(__file__).resolve().parent / ".coupang_partner_profile"
SHORT_PATTERN = re.compile(r"^/a/[A-Za-z0-9]+/?$")


def validate_product_url(url):
    """Require a DIRECT product URL, not somebody else's affiliate short link."""
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError("A direct, reasonably sized Coupang product URL is required")
    p = urlsplit(url.strip())
    if (p.scheme != "https" or p.hostname not in ("coupang.com", "www.coupang.com")
            or p.username is not None or p.password is not None
            or p.fragment or p.port not in (None, 443)
            or not re.fullmatch(r"/vp/products/\d+/?", p.path)):
        raise ValueError("Use the direct https://www.coupang.com/vp/products/<number> product URL, NOT a shortened affiliate URL")
    query = parse_qs(p.query, keep_blank_values=True)
    # Product id alone doesn't identify seller/option; fail closed rather
    # than silently advertising a different pack size or seller.
    for key in ("itemId", "vendorItemId"):
        values = query.get(key, [])
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError(f"Missing numeric {key} in direct URL; exact option cannot be verified")
    return url.strip()


def validate_personal_link(url):
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError("Generated Partners URL was invalid")
    p = urlsplit(url.strip())
    if (p.scheme != "https" or p.hostname != "link.coupang.com"
            or p.port not in (None, 443)
            or p.username is not None or p.password is not None
            or p.query or p.fragment or not SHORT_PATTERN.fullmatch(p.path)):
        raise ValueError("Expected a standard https://link.coupang.com/a/<code> URL")
    return url.strip()



def validate_source_url(url):
    """A source must be one exact Coupang partner shortlink, never arbitrary URLs."""
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError("A Coupang affiliate shortlink is required")
    p = urlsplit(url.strip())
    if (p.scheme != "https" or p.hostname != "link.coupang.com"
            or p.port not in (None, 443) or p.username is not None
            or p.password is not None or p.fragment
            or not SHORT_PATTERN.fullmatch(p.path)):
        raise ValueError("Expected https://link.coupang.com/a/<code> (no extra fragment)")
    return url.strip()


def canonical_direct_product_url(url):
    """Resolve only an unambiguous product+item+seller option and drop referral tags.

    A resolved URL with just /vp/products/<id> is NOT enough to assert the
    original sale option, so never guess missing itemId/vendorItemId.
    """
    if not isinstance(url, str) or len(url) > 3000:
        raise ValueError("Coupang destination URL missing or oversized")
    p = urlsplit(url.strip())
    if p.scheme != "https" or p.username or p.password or p.fragment or p.port not in (None, 443):
        raise ValueError("Unsafe Coupang destination URL")
    if p.hostname in ("coupang.com", "www.coupang.com"):
        match = re.fullmatch(r"/vp/products/(\d+)/?", p.path)
    elif p.hostname == "m.coupang.com":
        match = re.fullmatch(r"/vm/products/(\d+)/?", p.path)
    else:
        raise ValueError("Shortlink did not resolve to a recognized Coupang product page")
    if not match:
        raise ValueError("Shortlink resolved to a category, promotion, or unsupported product route")
    query = parse_qs(p.query, keep_blank_values=True)
    options = {}
    for key in ("itemId", "vendorItemId"):
        value = query.get(key, [])
        if len(value) != 1 or not value[0].isdigit():
            raise ValueError("Could not confirm original product option: missing " + key)
        options[key] = value[0]
    return "https://www.coupang.com/vp/products/" + match.group(1) + "?" + urlencode(options)


def resolve_shortlink_once(playwright, url):
    """Open one source affiliate link in an isolated, throwaway browser.

    No cookie/profile data is shared with the personal Coupang Partners login.
    The navigation may count as a referral click; never repeat it in a loop
    or make purchases through one's own partner referral link.
    """
    validate_source_url(url)
    browser = playwright.chromium.launch(headless=False)
    try:
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1700)  # Allow ordinary JS redirects, not bot challenges.
            destination = page.url
            # No scraping of hidden data and no direct calls to undocumented APIs.
            cleaned = canonical_direct_product_url(destination)
            print("SOURCE_RESOLVED: confirmed Coupang product with itemId and vendorItemId")
            return cleaned
        finally:
            context.close()
    finally:
        browser.close()

def _first_visible_unique(locators):
    for loc in locators:
        try:
            visible = [loc.nth(i) for i in range(min(loc.count(), 10)) if loc.nth(i).is_visible()]
            if len(visible) == 1:
                return visible[0]
        except Exception:
            continue
    return None


def _fill_product_input(page, url):
    locators = [
        page.get_by_label("쿠팡 URL", exact=False),
        page.get_by_placeholder("쿠팡 URL", exact=False),
        page.locator('input[aria-label*="쿠팡 URL"]'),
        page.locator('input[placeholder*="쿠팡 URL"]'),
        page.locator('input[name="url"]'),
    ]
    item = _first_visible_unique(locators)
    if item is None:
        return False
    item.fill(url, timeout=5000)
    return item.input_value() == url


def _click_generate(page):
    locators = [
        page.get_by_role("button", name="링크 생성", exact=True),
        page.get_by_role("button", name="링크생성", exact=True),
        page.locator('button:has-text("링크 생성")'),
        page.locator('button:has-text("링크생성")'),
    ]
    button = _first_visible_unique(locators)
    if button is None:
        return False
    button.click(timeout=5000)
    return True


def _find_generated_link(page):
    """Read only standard affiliate links from visible page fields/anchors."""
    candidates = []
    for field in page.locator("input, textarea").all():
        try:
            if field.is_visible():
                value = field.input_value(timeout=1000).strip()
                if value.startswith("https://link.coupang.com/a/"):
                    candidates.append(value)
        except Exception:
            pass
    for element in page.locator("a[href^='https://link.coupang.com/a/']").all():
        try:
            if element.is_visible():
                candidates.append(element.get_attribute("href"))
        except Exception:
            pass
    valid = []
    for candidate in candidates:
        try:
            link = validate_personal_link(candidate)
            if link not in valid:
                valid.append(link)
        except (ValueError, TypeError):
            pass
    # More than one result is ambiguous; do not silently pick a random URL.
    return valid[0] if len(valid) == 1 else None


def main():
    parser = argparse.ArgumentParser(description="Local assisted test of one Coupang Partners quick link; sends no Telegram alerts.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source-url", help="Original https://link.coupang.com/a/... affiliate shortlink")
    selection.add_argument("--product-url", help="Direct Coupang product URL with itemId and vendorItemId")
    args = parser.parse_args()
    try:
        if args.source_url:
            validate_source_url(args.source_url)
            product_url = None
        else:
            product_url = canonical_direct_product_url(validate_product_url(args.product_url))
    except (ValueError, OverflowError) as exc:
        parser.error(str(exc))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install Playwright with: python -m pip install playwright && python -m playwright install chromium")
        return 2

    print("ONE-PRODUCT LOCAL TEST: no Telegram delivery and no repository updates.")
    print("A separate browser will open. Sign in to Coupang Partners MANUALLY.")
    print("DO NOT paste cookies/passwords/OTP/API tokens into GitHub or the chat.")
    print("If challenged with CAPTCHA or access restrictions, STOP. Do not bypass them.")
    print("Use the Partners menu '링크 생성 > 간편 링크 만들기'.")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        if args.source_url:
            print("The original partner link will be opened ONCE in an isolated browser.")
            print("This may register a partner referral click. Do not self-purchase or repeatedly click.")
            if input("Run one-link source resolution test? Type YES to proceed: ").strip() != "YES":
                print("Cancelled; no URL visited.")
                return 0
            try:
                product_url = resolve_shortlink_once(playwright, args.source_url)
            except Exception as exc:
                print("SOURCE_UNRESOLVED:", type(exc).__name__ + ": " + str(exc)[:180])
                print("No partner URL generated. If the link opens an app or hides item/vendor IDs,")
                print("open it manually, choose the exact option, then run --product-url with the full destination.")
                return 1
        print("Verified option-bearing Coupang destination:", product_url)
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1280, "height": 880}
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(PARTNERS_URL, wait_until="domcontentloaded", timeout=30000)
            input("\n[1/3] 브라우저에서 직접 로그인하고 '간편 링크 만들기' 화면을 열어. 완료하면 CMD에서 Enter: ")
            if urlsplit(page.url).hostname not in ("partners.coupang.com", "www.partners.coupang.com"):
                print("Not on the official Partners site; aborting.")
                return 1

            auto_filled = _fill_product_input(page, product_url)
            if auto_filled:
                print("[2/3] 상품 URL 자동 입력 성공.")
            else:
                print("[2/3] 입력칸이 확인되지 않아 자동 입력을 멈춤. 아래 상품 URL을 공식 화면의 '쿠팡 URL' 칸에 직접 붙여넣어.")
                print(product_url)
                input("정확히 입력했으면 Enter: ")

            if auto_filled and _click_generate(page):
                print("[3/3] 링크 생성 버튼 자동 클릭 성공.")
            else:
                print("[3/3] 화면에서 공식 '링크 생성' 버튼을 직접 눌러.")
                input("링크가 생성된 다음 Enter: ")

            result = None
            for _ in range(15):
                result = _find_generated_link(page)
                if result:
                    break
                time.sleep(1)
            if not result:
                print("생성된 제휴 링크를 확실히 판독하지 못했어. 임의의 링크를 선택하거나 전송하지 않음.")
                print("화면에서 URL 복사로 직접 확인하고, 로그인 정보는 절대 공유하지 마.")
                return 1
            print("\n=== 제휴 링크 형식 검증 성공 (상품 동일성은 아직 사람이 확인해야 함) ===")
            print(result)
            print("원본 제품의 옵션·수량·판매자가 정확히 같은지 브라우저 화면에서 확인해.")
            print("원본 링크를 1회 확인했을 수 있지만 구매 클릭/텔레그램 전송은 하지 않았음.")
            input("결과를 확인했으면 Enter를 눌러 브라우저 종료: ")
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    sys.exit(main())
