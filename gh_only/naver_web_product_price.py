"""Public Naver *general search* first shopping-card price; no Naver API.

This is a ONE-TIME comparison quote from a Naver search result, not a
Lowchart historical average, not an official Coupang usual price. The first
shopping product can differ from the requested product/option.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlsplit
import re

NAVER_HOST = "m.search.naver.com"
MAX_PRICE = 100_000_000
PRICE_TEXT = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d{4,8})\s*원?$")
SHOPPING_LINK_HOST = "cr3.shopping.naver.com"

# Keep detection tied to Naver's outbound shopping result cards. Other
# visible amounts (blogs, advertising copy, snippets) are not prices.
SHOPPING_CARD_JS = r"""() => {
  const priceRx = /^\s*(?:\d{1,3}(?:,\d{3})+|\d{4,8})\s*원?\s*$/;
  for (const node of document.querySelectorAll('span,strong,em')) {
    const shown = (node.innerText || '').trim();
    if (!priceRx.test(shown)) continue;
    let ancestor = node;
    for (let depth = 0; ancestor && depth <= 8; depth++, ancestor = ancestor.parentElement) {
      const link = ancestor.querySelector(
        'a[href^="https://cr3.shopping.naver.com/v2/bridge/searchGate"],' +
        'a[href^="https://cr.shopping.naver.com/"]'
      );
      if (link && ancestor.innerText.length < 2200) {
        return {price_text: shown, card_text: ancestor.innerText.slice(0, 500),
                shopping_link: link.href};
      }
    }
  }
  return null;
}"""


def parse_first_card(row: dict | None) -> int | None:
    """Return the *first* price in an anchored shopping card, not snippet text."""
    if not isinstance(row, dict):
        return None
    val = row.get("price_text")
    link = row.get("shopping_link")
    card = row.get("card_text")
    if not isinstance(val, str) or not PRICE_TEXT.fullmatch(val.strip()):
        return None
    if not isinstance(card, str) or len(card) > 1200:
        return None
    if not isinstance(link, str):
        return None
    try:
        u = urlsplit(link)
        if (u.scheme != "https" or u.hostname not in (
                "cr3.shopping.naver.com", "cr.shopping.naver.com")
                or u.username or u.password):
            return None
        if (u.hostname == "cr3.shopping.naver.com"
                and not u.path.startswith("/v2/bridge/searchGate")):
            return None
    except ValueError:
        return None
    amount = int(re.sub(r"\D", "", val))
    return amount if 100 <= amount <= MAX_PRICE else None


def fetch_first_naver_product_price(title: str, *, chromium=None) -> dict:
    """One read-only general search, first grounded shopping-card price.

    Fails closed on blocked pages or missing card. No login, proxies, private
    endpoints, shopping API or click-through to a store.
    """
    out = {
        "status": "NAVER_PRICE_UNAVAILABLE",
        "price_krw": None,
        "price_source": "naver_general_search_first_shopping_card",
        "price_is_historical_average": False,
        "product_option_verified": False,
    }
    if not isinstance(title, str) or not 4 <= len(title.strip()) <= 200:
        return {**out, "status": "NAVER_PRICE_TITLE_INVALID"}
    url = "https://m.search.naver.com/search.naver?" + urlencode(
        {"query": title.strip()})
    try:
        if chromium is not None:
            return _load_price(chromium, url, out)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                return _load_price(browser, url, out)
            finally:
                browser.close()
    except Exception as exc:
        return {**out, "status": "NAVER_PRICE_BROWSER_ERROR",
                "error_type": type(exc).__name__}


def _load_price(browser, url: str, out: dict) -> dict:
    page = browser.new_page(
        locale="ko-KR", viewport={"width": 1300, "height": 950})
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=18000)
        observed = urlsplit(page.url)
        if (response is None or response.status != 200
                or observed.scheme != "https" or observed.hostname != NAVER_HOST
                or observed.path != "/search.naver"):
            return {**out, "status": "NAVER_PRICE_SEARCH_BLOCKED"}
        page.wait_for_timeout(1600)
        raw = page.evaluate(SHOPPING_CARD_JS)
        if raw is None:
            # Search shopping cards can mount after the first content paint.
            page.mouse.wheel(0, 700)
            page.wait_for_timeout(850)
            raw = page.evaluate(SHOPPING_CARD_JS)
        price = parse_first_card(raw)
        if price is None:
            return {**out, "status": "NAVER_PRICE_CARD_NOT_FOUND"}
        return {**out, "status": "PRICE_FOUND", "price_krw": price,
                "search_url": url, "result_url": raw["shopping_link"],
                "result_title": raw["card_text"].splitlines()[0][:180]}
    finally:
        page.close()
