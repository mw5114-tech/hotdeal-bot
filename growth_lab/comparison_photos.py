"""Comparison photos from exact Lowchart items or Naver Shopping product cards."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import os
import re
import unicodedata
from urllib import error, parse, request

from api_radar.quality import extract_exact_identity, NoRedirect
from gh_only.default_deal_format import normalized_product_name
from gh_only.lowchart_product_photo import (
    lookup_exact_item_photo, download_exact_photo,
)

NAVER_SEARCH = "https://m.search.naver.com/search.naver"
MAX_IMAGE = 2_500_000
MAX_RESPONSE = 4_000_000


def exact_identity(product: dict, radar: dict) -> dict | None:
    pid = product.get("id")
    if not isinstance(pid, str):
        return None
    direct = extract_exact_identity(product.get("url"), pid)
    if direct:
        return {**direct, "status": "EXACT_ITEM_IDENTIFIED"}
    known = product.get("identity")
    if (isinstance(known, dict) and known.get("product_id") == pid
            and known.get("status") == "EXACT_ITEM_IDENTIFIED"
            and re.fullmatch(r"\d{5,18}", str(known.get("item_id", "")))):
        return known
    # The official SEARCH API sometimes returns a product-level opaque URL.
    # Reuse only an already observed exact-item URL of the SAME product ID
    # and matching product name, never invent a missing item ID.
    inventory = radar.get("goldbox_today") or {}
    row = (inventory.get("items") or {}).get(pid)
    history = (radar.get("products") or {}).get(pid)
    candidates = []
    if isinstance(row, dict):
        candidates.append((row.get("name"), row.get("url")))
    if isinstance(history, dict):
        candidates.append((history.get("name"), history.get("last_product_url")))
    name = normalized_product_name(product.get("name"))
    for known_name, url in candidates:
        if not name or normalized_product_name(known_name) != name:
            continue
        found = extract_exact_identity(url, pid)
        if found:
            return {**found, "status": "EXACT_ITEM_IDENTIFIED"}
    return None


def lowchart_photo(product: dict, radar: dict, folder: Path) -> dict:
    identity = exact_identity(product, radar)
    if identity is None:
        return {"status": "LOWCHART_EXACT_ITEM_ID_UNAVAILABLE"}
    info = lookup_exact_item_photo(identity, product["name"])
    if info.get("status") != "LOWCHART_EXACT_ITEM_IMAGE":
        status = info.get("status", "LOWCHART_IMAGE_NOT_FOUND")
        if type(info.get("http_status")) is int:
            status += "_HTTP_" + str(info["http_status"])
        return {"status": status}
    # An exact Lowchart page may have a different product-name/option label.
    # For a side-by-side comparison, don't show it as the chosen product
    # unless the existing title/option matcher agrees.
    if info.get("image_same_named_option") is not True:
        return {"status": "LOWCHART_COMPARISON_TITLE_MISMATCH"}
    folder.mkdir(parents=True, exist_ok=True)
    result = download_exact_photo(info["og_image"], folder, int(product["id"]))
    if result.get("status") == "IMAGE_SAVED":
        return {"status": "IMAGE_SAVED", "path": folder / result["saved_name"],
                "source": "lowchart_exact_item_public_page"}
    return {"status": result.get("status", "LOWCHART_DOWNLOAD_UNKNOWN")}


def _https_location(url: str | None):
    if not isinstance(url, str) or not 1 <= len(url) <= 3000:
        return None
    if any(ord(c) < 33 for c in url):
        return None
    try:
        u = parse.urlsplit(url)
        if (u.scheme == "https" and u.port in (None, 443) and not u.username
                and not u.password and not u.fragment):
            return u
    except ValueError:
        pass
    return None


def _shopping_card(url: str | None) -> bool:
    u = _https_location(url)
    return u is not None and (
        (u.hostname == "cr3.shopping.naver.com" and u.path == "/v2/bridge/searchGate")
        or (u.hostname == "search.shopping.naver.com"
            and re.fullmatch(r"/(?:ns/)?catalog/\d+", u.path) is not None)
    )


def _shopping_image(url: str | None) -> bool:
    u = _https_location(url)
    return (u is not None and u.hostname == "shopping-phinf.pstatic.net"
            and re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:jpe?g|png|webp)", u.path, re.I) is not None
            and (not u.query or re.fullmatch(r"type=[A-Za-z0-9_]+", u.query) is not None))


def title_match(product: str, card_title: str) -> bool:
    # Preserve punctuation inside option/model tokens (2.5 vs 25, G-304
    # vs G304). Never strip text after a hyphen or use a substring match.
    def tokens(value: str) -> list[str]:
        return re.findall(r"[^\W_]+(?:[./+_-][^\W_]+)*",
                          unicodedata.normalize("NFKC", value).casefold())
    a, b = tokens(product), tokens(card_title)
    return sum(map(len, a)) >= 6 and a == b


class _ShoppingCards(HTMLParser):
    def __init__(self, title: str):
        super().__init__(convert_charrefs=True)
        self.title = title
        self.in_card = False
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            self.in_card = _shopping_card(values.get("href"))
        if tag != "img" or not self.in_card or len(self.images) >= 5:
            return
        alt = values.get("alt") or ""
        if not title_match(self.title, alt):
            return
        src = values.get("src")
        if _shopping_image(src) and src not in self.images:
            self.images.append(src)

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_card = False


def _search_cards(title: str, query: str, *, opener=None) -> dict:
    url = NAVER_SEARCH + "?" + parse.urlencode({"query": query})
    req = request.Request(url, headers={
        "Accept": "text/html", "Accept-Language": "ko-KR,ko;q=0.9",
    })
    op = opener or request.build_opener(NoRedirect()).open
    try:
        with op(req, timeout=12) as res:
            if (res.status != 200 or res.geturl() != url
                    or res.headers.get_content_type() != "text/html"):
                return {"status": "SHOPPING_RESPONSE_UNEXPECTED", "images": []}
            raw = res.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        return {"status": "SHOPPING_HTTP_" + str(exc.code), "images": []}
    except (error.URLError, OSError):
        return {"status": "SHOPPING_NETWORK_ERROR", "images": []}
    if len(raw) > MAX_RESPONSE:
        return {"status": "SHOPPING_RESPONSE_OVERSIZE", "images": []}
    cards = _ShoppingCards(title)
    cards.feed(raw.decode("utf-8", errors="replace"))
    cards.close()
    return {"status": "SHOPPING_MATCH" if cards.images else "SHOPPING_NO_EXACT_CARD",
            "images": cards.images}


def shopping_photos(title: str, *, opener=None) -> dict:
    """Read public shopping cards; a shorter query never relaxes the title check."""
    if not isinstance(title, str) or not 6 <= len(title.strip()) <= 160:
        return {"status": "SHOPPING_TITLE_INVALID", "images": []}
    result = _search_cards(title, title.strip(), opener=opener)
    if result["status"] != "SHOPPING_NO_EXACT_CARD":
        return result
    for token in re.findall(r"(?<![0-9A-Za-z])[0-9A-Za-z][0-9A-Za-z-]{2,23}(?![0-9A-Za-z])", title):
        if (re.search(r"[a-z]", token, re.I) and re.search(r"[0-9]", token)
                and not re.fullmatch(r"[0-9]+(?:ml|l|kg|g|gb|tb|hz|w|v|mah|cm|mm)", token, re.I)
                and token.casefold() != title.strip().casefold()):
            return _search_cards(title, token, opener=opener)
    return result


def image_bytes(url: str, *, opener=None) -> dict:
    if not _shopping_image(url):
        return {"status": "SHOPPING_IMAGE_HOST_REJECTED"}
    req = request.Request(url, headers={"Accept": "image/jpeg,image/png,image/webp"})
    op = opener or request.build_opener(NoRedirect()).open
    try:
        with op(req, timeout=12) as res:
            if res.status != 200 or res.geturl() != url:
                return {"status": "SHOPPING_IMAGE_DOWNLOAD_UNEXPECTED"}
            mime = res.headers.get_content_type()
            raw = res.read(MAX_IMAGE + 1)
    except error.HTTPError as exc:
        return {"status": "SHOPPING_IMAGE_HTTP_" + str(exc.code)}
    except (error.URLError, OSError):
        return {"status": "SHOPPING_IMAGE_NETWORK_ERROR"}
    good = ((mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
            or (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
    if not good or not 1000 <= len(raw) <= MAX_IMAGE:
        return {"status": "SHOPPING_IMAGE_INVALID_BYTES"}
    return {"status": "SHOPPING_IMAGE_OK", "raw": raw, "mime": mime}


def choose_photo(a: dict, b: dict, radar: dict, folder: Path) -> dict:
    for label, product in (("A", a), ("B", b)):
        photo = lowchart_photo(product, radar, folder)
        print("TREND_LOWCHART_IMAGE product=" + label + " status=" + photo["status"])
        if photo["status"] == "IMAGE_SAVED":
            return {**photo, "label": label}
    # The comparison workflow has its own image policy: it may use
    # representative search images even if Lowchart is unavailable. The
    # Jirum unlisted-only fallback is unchanged.
    if os.getenv("TREND_COMPARISON_NAVER_IMAGE_API_ENABLED") == "true":
        from gh_only.naver_api_image_photo import fetch_matched_photo, matching_title
        for label, product in (("A", a), ("B", b)):
            pid, name = product.get("id"), product.get("name")
            if not isinstance(pid, str) or not re.fullmatch(r"\d{5,18}", pid):
                continue
            result = fetch_matched_photo(name, folder, int(pid))
            print("TREND_NAVER_IMAGE_API product=" + label + " status=" + result["status"])
            if (result.get("status") != "IMAGE_SAVED"
                    or result.get("representative_naver_api_image") is not True
                    or not matching_title(name, result.get("image_provider_title"))):
                continue
            filename = result.get("saved_name")
            if not isinstance(filename, str) or not re.fullmatch(
                    r"jirum_" + pid + r"_naver_api_photo\.(?:jpg|png|webp)", filename):
                continue
            target = folder / filename
            if not target.is_file() or not 1000 <= target.stat().st_size <= MAX_IMAGE:
                continue
            return {"status": "IMAGE_SAVED", "path": target,
                    "source": "naver_api_matched_image", "label": label}
    for label, product in (("A", a), ("B", b)):
        pid = product.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"\d{5,18}", pid):
            continue
        result = shopping_photos(product["name"])
        print("TREND_NAVER_SHOPPING product=" + label + " status=" + result["status"])
        for url in result["images"][:2]:
            data = image_bytes(url)
            print("TREND_NAVER_SHOPPING_DOWNLOAD product=" + label + " status=" + data["status"])
            if data["status"] != "SHOPPING_IMAGE_OK":
                continue
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[data["mime"]]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("naver_shopping_" + pid + ext)
            target.write_bytes(data["raw"])
            return {"status": "IMAGE_SAVED", "path": target,
                    "source": "naver_shopping_product_card", "label": label}
    return {"status": "NO_MATCHING_PRODUCT_IMAGE"}
