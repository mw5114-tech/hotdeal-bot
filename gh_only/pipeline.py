"""Isolated GitHub-only public deal enrichment prototype (read-only).

One HTTPS read from the public @jirum Telegram page per --mode live run.
Does not access Coupang pages, log in, publish posts, mint affiliate links,
or touch existing production state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from collections import Counter
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib import request
from urllib.parse import parse_qs, urlsplit

from jirum_forward import PUBLIC_URL, parse_listing

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{1,8})\s*원")
SALE_RE = re.compile(r"(?:판매가|최종가|특가|할인가|할인\s*가격|쿠폰\s*적용가|와우가|대박)\s*[:：]?\s*(\d[\d,]*)\s*원")
ORIGINAL_RE = re.compile(r"(?:정상가|정가|원가|정상가격|원래\s*가격|소비자가)\s*[:：]?\s*(\d[\d,]*)\s*원")
IMAGE_STYLE = re.compile(r"background-image\s*:\s*url\(\s*['\"]?(https://[^'\"\s)]+)", re.I)
POST_RE = re.compile(r"jirum/(\d+)")
NON_SALE = re.compile(r"배송비|적립|캐시|할인\s*쿠폰|쿠폰\s*할인|정상가|정가|원가|최대\s*할인|소비자가")


def amount(raw: str) -> int | None:
    try:
        value = int(raw.replace(",", ""))
        return value if 0 < value <= 100_000_000 else None
    except (TypeError, ValueError):
        return None


def price_info(text: str) -> dict:
    """Source-post labels only; do NOT imply live Coupang prices."""
    sales: set[int] = set()
    originals: set[int] = set()
    unlabeled: set[int] = set()
    for line in text.splitlines():
        for match in SALE_RE.finditer(line):
            value = amount(match.group(1))
            if value is not None:
                sales.add(value)
        for match in ORIGINAL_RE.finditer(line):
            value = amount(match.group(1))
            if value is not None:
                originals.add(value)
        if not NON_SALE.search(line) and not SALE_RE.search(line) and not ORIGINAL_RE.search(line):
            matches = [amount(m.group(1)) for m in PRICE_RE.finditer(line)]
            unlabeled.update(p for p in matches if p is not None)
    if len(sales) == 1:
        sale = next(iter(sales))
        kind = "labeled_telegram_price"
    elif len(sales) > 1:
        sale, kind = None, "conflicting_telegram_prices"
    elif len(unlabeled) == 1:
        sale, kind = next(iter(unlabeled)), "unlabeled_telegram_price"
    else:
        sale = None
        kind = "conflicting_telegram_prices" if len(unlabeled) > 1 else "no_price_in_post"
    original = next(iter(originals)) if len(originals) == 1 else None
    if original is not None and sale is not None and original <= sale:
        original = None
    discount = round((original - sale) * 100 / original, 1) if sale and original else None
    return {"sale_price_krw": sale, "original_price_krw": original,
            "discount_percent_computed": discount, "price_provenance": "telegram_source_post",
            "price_confidence": kind, "original_price_confirmed": original is not None}


def image_url(url: str) -> str | None:
    """Only accept an explicitly attached Telegram photo, never preview thumbnails."""
    cleaned = unescape(url.strip())
    try:
        u = urlsplit(cleaned)
    except ValueError:
        return None
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.port not in (None, 443) or len(cleaned) > 2500):
        return None
    return cleaned


class PostPhotoParser(HTMLParser):
    """Read photo_wrap CSS inside exact Telegram data-post boundaries."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.post_id = None
        self.post_depth = None
        self.photos: dict[int, list[str]] = {}
        self.previews: dict[int, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == "div":
            self.depth += 1
            if self.post_id is None:
                found = POST_RE.fullmatch(props.get("data-post", ""))
                if found:
                    self.post_id = int(found.group(1))
                    self.post_depth = self.depth
        if self.post_id is not None and tag in ("a", "div", "span", "img"):
            classes = props.get("class", "").split()
            found = IMAGE_STYLE.search(props.get("style", ""))
            raw = found.group(1) if found else props.get("src", "") if tag == "img" else ""
            photo = image_url(raw) if raw else None
            if photo and "tgme_widget_message_photo_wrap" in classes:
                if photo not in self.photos.setdefault(self.post_id, []):
                    self.photos[self.post_id].append(photo)
            elif photo and "link_preview_right_image" in classes:
                if photo not in self.previews.setdefault(self.post_id, []):
                    self.previews[self.post_id].append(photo)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.post_id is not None and self.depth == self.post_depth:
            self.post_id, self.post_depth = None, None
        self.depth = max(0, self.depth - 1)


class MarkupInspector(HTMLParser):
    """Only count public Telegram media element types; never log page HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.class_counts = Counter()
        self.inline_image_tags = 0
        self.background_styles = 0
        self.posted_photos = 0
        self._post_depth = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        if tag == "div":
            self._depth += 1
            if self._post_depth is None and POST_RE.fullmatch(props.get("data-post", "")):
                self._post_depth = self._depth
        if self._post_depth is not None:
            classes = props.get("class", "").split()
            if tag == "img":
                self.inline_image_tags += 1
            if "background-image" in props.get("style", ""):
                self.background_styles += 1
            for cls in classes:
                if any(key in cls for key in ("photo", "image", "media", "preview")):
                    self.class_counts[cls] += 1
                    if cls == "tgme_widget_message_photo_wrap":
                        self.posted_photos += 1

    def handle_endtag(self, tag):
        if tag == "div":
            if self._post_depth == self._depth:
                self._post_depth = None
            self._depth = max(0, self._depth - 1)


def identify_url(raw: str | None) -> dict:
    result = {"url_kind": "unknown", "product_id": None, "item_id": None,
              "vendor_item_id": None, "exact_option_verified": False}
    if not raw:
        return result
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").lower()
        if u.scheme != "https" or u.username or u.password:
            return result
        if host == "link.coupang.com" and re.fullmatch(r"/a/[A-Za-z0-9]+/?", u.path):
            result["url_kind"] = "coupang_shortlink"
        elif host in ("www.coupang.com", "coupang.com"):
            found = re.fullmatch(r"/vp/products/(\d+)/?", u.path)
            if found:
                result["url_kind"] = "coupang_product"
                result["product_id"] = found.group(1)
                query = parse_qs(u.query)
                for key, output_key in (("itemId", "item_id"), ("vendorItemId", "vendor_item_id")):
                    values = query.get(key, [])
                    if len(values) == 1 and values[0].isdigit():
                        result[output_key] = values[0]
        else:
            result["url_kind"] = "external"
    except ValueError:
        pass
    return result


def enrich_posts(html: str, max_posts: int = 8) -> list[dict]:
    posts = parse_listing(html)
    media = PostPhotoParser()
    media.feed(html)
    deals = []
    for post in posts[-max_posts:]:
        photos = media.photos.get(post["id"], [])
        previews = media.previews.get(post["id"], [])
        chosen_image = photos[0] if len(photos) == 1 else (
            previews[0] if not photos and len(previews) == 1 else None
        )
        image_source = ("telegram_attached_photo" if len(photos) == 1
                        else "telegram_link_preview_unverified" if not photos and len(previews) == 1
                        else "ambiguous_telegram_images" if len(photos) > 1 or len(previews) > 1
                        else "photo_not_visible")
        prices = price_info(post["text"])
        ids = identify_url(post["product_url"])
        deals.append({
            "source": "public_telegram_jirum", "source_message_id": post["id"],
            "source_post_url": f"https://t.me/jirum/{post['id']}",
            "title": post["title"], "original_purchase_url": post["product_url"],
            "image_url_candidate": chosen_image,
            "image_provenance": image_source,
            "image_verified_for_exact_option": False,
            "own_affiliate_url": None, "auto_publish_ready": False,
            "blockers": [
                *([] if prices["sale_price_krw"] else ["PRICE_UNAVAILABLE"]),
                *([] if chosen_image else ["SINGLE_SOURCE_IMAGE_UNAVAILABLE"]),
                *([] if image_source == "telegram_attached_photo" else ["IMAGE_NOT_FROM_ORIGINAL_POST_PHOTO"]),
                "EXACT_OPTION_NOT_VERIFIED", "OWN_AFFILIATE_LINK_NOT_CREATED",
                "PUBLISHER_NOT_INTEGRATED",
            ],
            **prices, **ids
        })
    return deals


def fetch_public_html() -> str:
    req = request.Request(PUBLIC_URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; PublicDealReader/1.0)",
        "Accept": "text/html",
    })
    with request.urlopen(req, timeout=25) as response:
        if urlsplit(response.geturl()).hostname != "t.me":
            raise RuntimeError("PUBLIC_CHANNEL_REDIRECT")
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise RuntimeError("PUBLIC_CHANNEL_TOO_LARGE")
    return data.decode("utf-8-sig", "replace")


def run(mode: str, output: Path, html_path: Path | None = None):
    if mode == "fixture":
        if not html_path:
            raise ValueError("fixture_html_required")
        html = html_path.read_text(encoding="utf-8")
    else:
        html = fetch_public_html()
    deals = enrich_posts(html)
    inspector = MarkupInspector()
    inspector.feed(html)
    report = {
        "schema": "github-only-deal-probe.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "source": "https://t.me/s/jirum",
        "total_posts_sampled": len(deals),
        "post_photos_available": sum(d["image_provenance"] == "telegram_attached_photo" for d in deals),
        "link_preview_images_available": sum(d["image_provenance"] == "telegram_link_preview_unverified" for d in deals),
        "image_candidates_available": sum(d["image_url_candidate"] is not None for d in deals),
        "post_sale_prices_available": sum(d["sale_price_krw"] is not None for d in deals),
        "post_original_prices_available": sum(d["original_price_krw"] is not None for d in deals),
        "exact_options_verified": 0, "affiliate_links_generated": 0,
        "posts_published": 0,
        "media_markup_diagnostic": {
            "inline_image_tags": inspector.inline_image_tags,
            "background_style_elements": inspector.background_styles,
            "photo_wrap_elements": inspector.posted_photos,
            "relevant_class_counts": dict(inspector.class_counts.most_common(12)),
        },
        "deals": deals,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "deals"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--fixture-html")
    parser.add_argument("--output", default="gh_only_report.json")
    options = parser.parse_args()
    run(options.mode, Path(options.output), Path(options.fixture_html) if options.fixture_html else None)
