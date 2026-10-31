"""One-shot, unauthenticated Coupang product-page readability probe.

Does not use login, proxies, stealth settings, CAPTCHA solving, account actions,
Telegram, or the existing production automation. Results are diagnostic only.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

PRODUCT_ID = "9412434217"
ITEM_ID = "25858815414"
URL = "https://www.coupang.com/vp/products/9412434217?itemId=25858815414&src=1139000&spec=10799999&addtag=400&ctag=9412434217&lptag=AF4625512&itime=20261031205813&pageType=PRODUCT&pageValue=9412434217&wPcid=17933502057510475327323&wRef=&wTime=20261031205813&redirect=landing&traceid=V0-183-d46e2ea51a9a840a&mcid=5f3a77a170234f7187dfd5741d13f092&pt=&campaignid=&clickBeacon=&imgsize=&slot=&pageid=&sig=&subid=telegram1&campaigntype=&puid=&ctime=&portal=&track=&landing_exp=&placementid=&puidType=&contentcategory=&tsource=&deviceid=&contenttype=&token=&impressionid=&requestid=&contentkeyword=&offerId=&sfId=&subparam="
OUTPUT = Path("coupang_probe_original_url.json")


def flatten_ld(value):
    if isinstance(value, list):
        for item in value:
            yield from flatten_ld(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from flatten_ld(value["@graph"])


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:400] or None


def first_text(page, selectors):
    for selector in selectors:
        try:
            nodes = page.locator(selector)
            for i in range(min(nodes.count(), 3)):
                text = clean_text(nodes.nth(i).inner_text(timeout=1500))
                if text:
                    return {"value": text, "selector": selector}
        except Exception:
            continue
    return None


def meta(page, selector):
    try:
        val = page.locator(selector).first.get_attribute("content", timeout=1200)
        return clean_text(val) if val else None
    except Exception:
        return None


def extract(page):
    title = clean_text(page.title())
    body_excerpt = clean_text(page.locator("body").inner_text(timeout=3000)) or ""
    access_denied = any(term in (title or "") + " " + body_excerpt
                        for term in ["사용권한이 없습니다", "사용권한이 제한된", "Access Denied", "Forbidden"])
    out = {
        "page_title": title,
        "access_denied_text_detected": access_denied,
        "body_excerpt": body_excerpt[:240],
        "og_title": meta(page, 'meta[property="og:title"]'),
        "og_image": meta(page, 'meta[property="og:image"]'),
        "og_price": meta(page, 'meta[property="product:price:amount"]'),
        "name_candidate": first_text(page, [
            "h1.prod-buy-header__title", "h2.prod-buy-header__title",
            "h1", 'meta[name="twitter:title"]'
        ]),
        "sale_price_candidate": first_text(page, [
            "span.total-price > strong", ".prod-sale-price .total-price strong",
            ".prod-coupon-price .total-price strong", ".prod-sale-price strong"
        ]),
        "original_price_candidate": first_text(page, [
            ".prod-original-price", ".origin-price", ".base-price"
        ]),
        "discount_candidate": first_text(page, [
            ".prod-sale-price .discount-percentage", ".discount-percentage"
        ]),
        "json_ld_product": None,
    }
    try:
        for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue
            for entry in flatten_ld(parsed):
                kinds = entry.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if "Product" in kinds:
                    offers = entry.get("offers")
                    if isinstance(offers, list):
                        offers = offers[:3]
                    out["json_ld_product"] = {
                        "name": clean_text(entry.get("name")),
                        "image": entry.get("image"),
                        "offers": offers,
                    }
                    break
            if out["json_ld_product"]:
                break
    except Exception as exc:
        out["json_ld_error"] = type(exc).__name__
    return out


def main():
    report = {
        "target_product_id": PRODUCT_ID,
        "target_item_id": ITEM_ID,
        "mode": "github-windows-chrome-stable-original-url-no-login",
        "requested_url": URL,
        "http_status": None,
        "final_hostname": None,
        "access": "unconfirmed",
        "page": None,
        "error": None,
    }
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
            page = context.new_page()
            try:
                response = page.goto(URL, wait_until="domcontentloaded", timeout=25000)
                report["http_status"] = response.status if response else None
                report["final_hostname"] = urlsplit(page.url).hostname
                page.wait_for_timeout(1000)
                report["page"] = extract(page)
                blocked = report["page"]["access_denied_text_detected"] or report["http_status"] in (401, 403, 429, 503)
                report["access"] = "blocked" if blocked else ("loaded" if report["http_status"] == 200 else "unconfirmed")
            except PlaywrightTimeoutError:
                report["access"] = "timeout"
                report["error"] = "navigation_timeout"
            finally:
                context.close()
    except Exception as exc:
        report["access"] = "error"
        report["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
    finally:
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("=== COUPANG READ-ONLY PROBE ===")
        print(OUTPUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
