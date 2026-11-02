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
           "og_url": None, "og_image": None}
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
    if not observed:
        out["status"] = "LOWCHART_PAGE_PRODUCT_TITLE_MISSING"
        return out
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
            "image_source": "lowchart_exact_item_public_page",
            "image_same_named_option": source_title_matches,
            "image_match_basis": "EXACT_LOWCHART_PRODUCT_ID_AND_ITEM_ID"}


def lookup_exact_item_photo(identity: dict, telegram_title: str, opener=None) -> dict:
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
    except Exception as exc:
        return {"status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                "error_type": type(exc).__name__, "og_image": None}
    if len(content) > MAX_HTML_BYTES:
        return {"status": "LOWCHART_PAGE_TOO_LARGE", "og_image": None}
    return parse_item_photo(content.decode("utf-8", "replace"), identity, telegram_title)


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
