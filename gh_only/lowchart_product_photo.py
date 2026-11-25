"""Read-only exact-item Lowchart product-photo source, no account/session.

Lowchart's own chart OG image is NOT a product photo. Accept exactly one
product <img> whose alt equals its exact-item page title, with a real
Coupang CDN image URL. Product and item IDs must match the known resolved
shortlink. Telegram title/option may differ from Lowchart's item naming:
it is NOT an image eligibility gate. The photo still must be the unique
Coupang CDN product image whose alt agrees with the exact-item page title.
No direct Coupang product-page requests are performed.
"""
from __future__ import annotations

from html.parser import HTMLParser
import os
import time
import re
from urllib import request, error
from urllib.parse import urlsplit

from gh_only.default_deal_format import normalized_product_name, same_named_option
from gh_only.lowchart_price_reference import build_url
from gh_only.shortlink_probe import StopRedirect

MAX_HTML_BYTES = 1_800_000
MAX_PHOTO_BYTES = 2_500_000
IMAGE_HOST = re.compile(r"(?:thumbnail\d*|image)\.coupangcdn\.com\Z", re.I)


def is_coupang_cdn_url(url: str | None) -> bool:
    if not isinstance(url, str) or not 10 < len(url) <= 2500:
        return False
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and bool(IMAGE_HOST.fullmatch(u.hostname or ""))
                and u.username is None and u.password is None
                and u.port in (None, 443) and not u.fragment
                and bool(re.search(r"\.(?:jpe?g|png|webp)$", u.path, re.I)))
    except ValueError:
        return False


class _ProductPictureHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "img":
            self.images.append({
                "alt": fields.get("alt", ""),
                "src": fields.get("src") or fields.get("data-src") or "",
            })

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, value):
        if self.in_title:
            self.title_parts.append(value)


def parse_item_photo(html: str, identity: dict, telegram_title: str) -> dict:
    """Trust exact product/item IDs, not Telegram-versus-Lowchart option text."""
    out = {"status": "LOWCHART_ITEM_NOT_VERIFIED", "og_title": None,
           "og_url": None, "og_image": None, "lowchart_title_verified": False}
    pid, iid = identity.get("product_id"), identity.get("item_id")
    if (identity.get("status") != "EXACT_ITEM_IDENTIFIED"
            or not isinstance(pid, str) or not isinstance(iid, str)):
        return out
    url = build_url(pid, iid)
    if not (pid in html and iid in html):
        out["status"] = "LOWCHART_ITEM_IDS_MISSING"
        return out
    parser = _ProductPictureHTML()
    parser.feed(html)
    page_title = "".join(parser.title_parts).strip()
    # Lowchart's title is '<exact item title> - <price> - 로우차트 ...'.
    item_title = re.split(r"\s+-\s+", page_title, maxsplit=1)[0].strip()
    out["og_title"] = item_title
    source = normalized_product_name(telegram_title)
    observed = normalized_product_name(item_title)
    # The resolved shortlink and exact Lowchart item path supply productId and
    # itemId identity. Do NOT reject because Telegram has another option name,
    # package count or color; the user explicitly permits those differences.
    # Do require a real page product title to choose its own main product photo,
    # rather than a recommendation, banner or price-history chart image.
    if (not observed or re.fullmatch(r"[-–]?\s*[\d,]+\s*원", item_title)):
        out["status"] = "LOWCHART_PAGE_PRODUCT_TITLE_MISSING"
        return out
    # The page is bound to the exact resolved product/item IDs. The
    # product name is independently verified even if its image is missing.
    out["og_url"] = url
    out["lowchart_title_verified"] = True
    source_title_matches = bool(
        source and (source == observed or same_named_option(telegram_title, item_title))
    )
    selected = [
        im["src"] for im in parser.images
        if normalized_product_name(im["alt"]) == observed
        and is_coupang_cdn_url(im["src"])
    ]
    if len(selected) != 1:
        out["status"] = ("LOWCHART_PRODUCT_IMAGE_AMBIGUOUS"
                         if selected else "LOWCHART_PRODUCT_IMAGE_NOT_FOUND")
        return out
    return {"status": "LOWCHART_EXACT_ITEM_IMAGE", "og_title": item_title,
            "og_url": url, "og_image": selected[0],
            "lowchart_title_verified": True,
            "image_source": "lowchart_exact_item_public_page",
            "image_same_named_option": source_title_matches,
            "image_match_basis": "EXACT_LOWCHART_PRODUCT_ID_AND_ITEM_ID"}


def _lookup_exact_item_photo_once(identity: dict, telegram_title: str, opener=None) -> dict:
    pid, iid = identity.get("product_id"), identity.get("item_id")
    if identity.get("status") != "EXACT_ITEM_IDENTIFIED":
        return {"status": "LOWCHART_ITEM_UNRESOLVED", "og_image": None}
    try:
        url = build_url(pid, iid)
    except ValueError:
        return {"status": "LOWCHART_ITEM_ID_INVALID", "og_image": None}
    opener = opener or request.build_opener(StopRedirect()).open
    req = request.Request(url, headers={
        "Accept": "text/html", "User-Agent": "Mozilla/5.0 (compatible; HotDealItemPhoto/1.0)",
    })
    try:
        with opener(req, timeout=22) as resp:
            landed = urlsplit(resp.geturl())
            if (resp.status != 200 or resp.geturl() != url
                or landed.scheme != "https"
                or landed.hostname not in ("lowchart.com", "www.lowchart.com")):
                return {"status": "LOWCHART_UNEXPECTED_RESPONSE", "og_image": None}
            content = resp.read(MAX_HTML_BYTES + 1)
    except error.HTTPError as exc:
        return {"status": ("LOWCHART_ITEM_PAGE_NOT_FOUND" if exc.code in (404, 410)
                           else "LOWCHART_IMAGE_PAGE_REQUEST_ERROR"),
                "http_status": exc.code, "error_type": "HTTPError",
                "og_image": None}
    except Exception as exc:
        return {"status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                "error_type": type(exc).__name__, "og_image": None}
    if len(content) > MAX_HTML_BYTES:
        return {"status": "LOWCHART_PAGE_TOO_LARGE", "og_image": None}
    # The historical mean is displayed on this exact same public item page.
    # Reuse the successful 200-response HTML in memory for the price reader:
    # two independent HTTP requests previously caused 200(photo)/403(price).
    # The HTML is NEVER copied into a post, stored in GitHub or logged.
    html = content.decode("utf-8", "replace")
    # A 200 alone is not a missing-product proof. Accept only explicit
    # visible missing-product text on the exact route, not script strings,
    # generic errors, missing images, or missing IDs by themselves.
    class MissingText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.hidden = 0
            self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "template", "noscript"):
                self.hidden += 1
        def handle_endtag(self, tag):
            if tag in ("script", "style", "template", "noscript"):
                self.hidden = max(0, self.hidden - 1)
        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data.strip())
    out = parse_item_photo(html, identity, telegram_title)
    missing = MissingText()
    missing.feed(html)
    visible = re.sub(r"\s+", " ", " ".join(missing.parts))
    if (out.get("status") != "LOWCHART_EXACT_ITEM_IMAGE"
            and not out.get("lowchart_title_verified")
            and re.search(r"(?:등록되지 않은 상품(?:입니다)?|해당 상품(?:이|은) 등록되어 있지 않습니다|상품을 찾을 수 없습니다|상품이 존재하지 않습니다)", visible)):
        return {"status": "LOWCHART_CONFIRMED_UNLISTED_200",
                "http_status": 200, "og_image": None,
                "lowchart_title_verified": False}
    out["http_status"] = 200
    if (out.get("lowchart_title_verified") is True
            and out.get("og_url") == url
            and pid in html and iid in html):
        out["_verified_public_item_html"] = html
    return out


def lookup_exact_item_photo(identity: dict, telegram_title: str,
                            opener=None, *, sleeper=None, ready=None) -> dict:
    """Only retry a public exact-item 403, bounded by four total attempts."""
    delays = ((0, 3, 7, 14)
              if os.getenv("JIRUM_LOWCHART_403_RETRY_ENABLED") == "true"
              else (0,))
    sleeper = sleeper or time.sleep
    result = None
    for attempt, delay in enumerate(delays):
        if delay and ready is not None:
            # Inspect only the already-verified sibling cache during the
            # existing bounded backoff. Do not issue another provider request
            # once a complete, validated bundle is available.
            remaining = delay
            while remaining > 0:
                shared = ready()
                if shared is not None:
                    return shared
                part = min(2, remaining)
                sleeper(part)
                remaining -= part
            shared = ready()
            if shared is not None:
                return shared
        elif delay:
            sleeper(delay)
        result = _lookup_exact_item_photo_once(
            identity, telegram_title, opener=opener)
        if result.get("http_status") != 403:
            return result
        print("LOWCHART_PHOTO_HTTP_403 " + str({
            "attempt": attempt + 1, "max_attempts": len(delays),
        }))
    if ready is not None:
        shared = ready()
        if shared is not None:
            return shared
    result["http_403_attempts"] = len(delays)
    result["retry_exhausted"] = True
    return result


def download_exact_photo(url: str | None, output, source_message_id: int,
                         opener=None) -> dict:
    out = {"status": "NOT_DOWNLOADED", "saved_name": None, "bytes": 0}
    if not is_coupang_cdn_url(url):
        out["status"] = "LOWCHART_IMAGE_HOST_REJECTED"
        return out
    op = opener or request.build_opener(StopRedirect()).open
    req = request.Request(url, headers={"Accept": "image/*",
                                        "User-Agent": "Mozilla/5.0"})
    try:
        with op(req, timeout=25) as resp:
            if resp.status != 200 or resp.geturl() != url:
                out["status"] = "LOWCHART_IMAGE_REDIRECT_OR_STATUS"
                return out
            mime = resp.headers.get_content_type()
            data = resp.read(MAX_PHOTO_BYTES + 1)
    except Exception as exc:
        out.update(status="LOWCHART_IMAGE_DOWNLOAD_FAILED",
                   error_type=type(exc).__name__)
        return out
    extension = {"image/jpeg": ".jpg", "image/png": ".png",
                 "image/webp": ".webp"}.get(mime)
    good = ((extension == ".jpg" and data.startswith(bytes((255, 216, 255))))
            or (extension == ".png" and data.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))))
            or (extension == ".webp" and data.startswith(b"RIFF")
                and data[8:12] == b"WEBP"))
    if not good or not 1000 <= len(data) <= MAX_PHOTO_BYTES:
        out["status"] = "LOWCHART_IMAGE_CONTENT_INVALID"
        return out
    filename = f"jirum_{source_message_id}_lowchart_photo{extension}"
    (output / filename).write_bytes(data)
    return {"status": "IMAGE_SAVED", "saved_name": filename,
            "bytes": len(data), "image_source": "lowchart_exact_item_public_page"}
