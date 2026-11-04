"""Read-only product-photo fallbacks: Couspi, then Dangapick.

A candidate is accepted only from the same Coupang product family, never from
search results for a different product. No login, screenshot, other sites, or
affiliate URLs are used. Images are downloaded from narrowly allowed hosts.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from pathlib import Path
from urllib import request, error
from urllib.parse import urlencode, urlsplit

from gh_only.default_deal_format import normalized_product_name
from gh_only.shortlink_probe import StopRedirect

MAX_HTML_BYTES = 1_800_000
MAX_INDEX_BYTES = 14_500_000
MAX_IMAGE_BYTES = 2_500_000
CDN_HOST = re.compile(r"(?:thumbnail\d*|image|t\d{1,2}[a-z])\.coupangcdn\.com\Z", re.I)
PIC_EXT = re.compile(r"\.(?:jpe?g|png|webp)$", re.I)
DANGA_SLUG = re.compile(r"/p/(\d{5,18})-(\d{5,18})-(\d{5,18})\Z")


def _compatible_title(expected: str, observed: str) -> bool:
    a, b = normalized_product_name(expected), normalized_product_name(observed)
    # Exact product ID is checked separately. Do not use a short generic name.
    return bool(len(a) >= 8 and len(b) >= 8 and
                (a == b or (len(a) >= 12 and a in b) or
                 (len(b) >= 12 and b in a)))


def _safe_image_url(value: str | None, provider: str, pid: str) -> bool:
    if not isinstance(value, str) or not 20 < len(value) <= 2500:
        return False
    try:
        u = urlsplit(value)
        if (u.scheme != "https" or u.username or u.password or u.fragment
                or u.port not in (None, 443) or not PIC_EXT.search(u.path)):
            return False
        if CDN_HOST.fullmatch(u.hostname or ""):
            return True
        return (provider == "couspi" and u.hostname == "image.couspi.com"
                and u.path.startswith(f"/cdn/{pid}/"))
    except ValueError:
        return False


class _ProductPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1 = []
        self._in_h1 = False
        self.images = []
        self.meta = {}
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1":
            self._in_h1 = True
        if tag == "img":
            self.images.append({
                "src": a.get("src") or a.get("data-src") or "",
                "alt": a.get("alt") or "",
            })
        if tag == "meta":
            k = a.get("property") or a.get("name")
            if isinstance(k, str):
                self.meta[k.lower()] = a.get("content") or ""
    def handle_endtag(self, tag):
        if tag == "h1":
            self._in_h1 = False
    def handle_data(self, data):
        if self._in_h1:
            self.h1.append(data)


def _fetch(url: str, host: str, size_limit: int, opener=None) -> bytes:
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname != host or u.username or u.password:
        raise ValueError("SOURCE_URL_REJECTED")
    op = opener or request.build_opener(StopRedirect()).open
    req = request.Request(url, headers={
        "Accept": "text/html,application/json",
        "User-Agent": "Mozilla/5.0 (compatible; ProductPhoto/1.0)",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    with op(req, timeout=18) as res:
        if res.status != 200 or res.geturl() != url:
            raise ValueError("SOURCE_REDIRECT_OR_STATUS")
        content = res.read(size_limit + 1)
    if len(content) > size_limit:
        raise ValueError("SOURCE_TOO_LARGE")
    return content


def _download(image: str, provider: str, pid: str, folder: Path, source_id: int,
              opener=None) -> dict:
    if not _safe_image_url(image, provider, pid):
        return {"status": "IMAGE_URL_REJECTED"}
    op = opener or request.build_opener(StopRedirect()).open
    req = request.Request(image, headers={
        "Accept": "image/*", "User-Agent": "Mozilla/5.0",
    })
    try:
        with op(req, timeout=22) as res:
            if res.status != 200 or res.geturl() != image:
                return {"status": "IMAGE_REDIRECT_OR_STATUS"}
            mime = res.headers.get_content_type()
            data = res.read(MAX_IMAGE_BYTES + 1)
    except Exception as exc:
        return {"status": "IMAGE_DOWNLOAD_FAILED",
                "error_type": type(exc).__name__}
    ext = {"image/jpeg": ".jpg", "image/png": ".png",
           "image/webp": ".webp"}.get(mime)
    valid = ((ext == ".jpg" and data.startswith(b"\xff\xd8\xff"))
             or (ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
             or (ext == ".webp" and data.startswith(b"RIFF")
                 and data[8:12] == b"WEBP"))
    if not valid or not 1000 <= len(data) <= MAX_IMAGE_BYTES:
        return {"status": "IMAGE_BYTES_INVALID"}
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"jirum_{source_id}_{provider}_photo{ext}"
    (folder / filename).write_bytes(data)
    return {"status": "IMAGE_SAVED", "saved_name": filename, "bytes": len(data)}


def _success(provider: str, image: str, title: str, pid: str, item: str | None,
             page: str, folder: Path, source_id: int, opener=None) -> dict:
    result = _download(image, provider, pid, folder, source_id, opener)
    if result.get("status") != "IMAGE_SAVED":
        return {"provider": provider, **result}
    return {"provider": provider, **result,
            "image_source": f"{provider}_product_page",
            "image_provider_verified": True,
            "image_provider_title": title,
            "image_provider_product_id": pid,
            "image_provider_item_id": item,
            "image_provider_page": page}


def lookup_couspi_photo(identity: dict, title: str, folder: Path, source_id: int,
                        opener=None) -> dict:
    """Couspi product-family page; vendor ID is Couspi's itemId parameter."""
    pid, vid = identity.get("product_id"), identity.get("vendor_item_id")
    if identity.get("status") != "EXACT_ITEM_IDENTIFIED" or not str(pid).isdigit():
        return {"provider": "couspi", "status": "PRODUCT_ID_MISSING"}
    page = f"https://www.couspi.com/products/{pid}"
    if isinstance(vid, str) and vid.isdigit():
        page += "?" + urlencode({"itemId": vid})
    try:
        html = _fetch(page, "www.couspi.com", MAX_HTML_BYTES, opener).decode(
            "utf-8", "replace")
    except Exception as exc:
        return {"provider": "couspi", "status": "SOURCE_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "http_status": getattr(exc, "code", None)}
    parser = _ProductPage()
    parser.feed(html)
    observed = "".join(parser.h1).strip()
    if not observed:
        observed = re.split(r"\s+\|\s+", parser.meta.get("og:title", ""))[0]
    if not _compatible_title(title, observed):
        return {"provider": "couspi", "status": "TITLE_MISMATCH"}
    candidates = [x["src"] for x in parser.images
                  if _safe_image_url(x["src"], "couspi", str(pid))
                  and _compatible_title(observed, x["alt"])]
    # On some page versions the main product image is exposed as og:image.
    meta = parser.meta.get("og:image", "")
    if not candidates and _safe_image_url(meta, "couspi", str(pid)):
        candidates = [meta]
    if not candidates:
        return {"provider": "couspi", "status": "IMAGE_NOT_FOUND"}
    return _success("couspi", candidates[0], observed, str(pid), None,
                    page, folder, source_id, opener)


def _dangapick_rows(payload: dict, pid: str, title: str) -> list[dict]:
    rows = payload.get("products")
    if not isinstance(rows, list) or len(rows) > 20000:
        return []
    matches = []
    for x in rows:
        if not isinstance(x, dict) or str(x.get("id")) != pid:
            continue
        if not _compatible_title(title, x.get("title", "")):
            continue
        iid, vid = str(x.get("item", "")), str(x.get("vendor", ""))
        if not (iid.isdigit() and vid.isdigit()):
            continue
        matches.append(x)
    return matches


def _dangapick_detail(identity: dict, row: dict, folder: Path,
                      source_id: int, opener=None) -> dict:
    pid = str(identity["product_id"])
    iid, vid = str(row["item"]), str(row["vendor"])
    page = f"https://dangapick.com/p/{pid}-{iid}-{vid}"
    try:
        html = _fetch(page, "dangapick.com", MAX_HTML_BYTES, opener).decode(
            "utf-8", "replace")
    except Exception as exc:
        return {"provider": "dangapick", "status": "DETAIL_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "http_status": getattr(exc, "code", None)}
    parser = _ProductPage()
    parser.feed(html)
    observed = "".join(parser.h1).strip()
    if not _compatible_title(row["title"], observed):
        return {"provider": "dangapick", "status": "DETAIL_TITLE_MISMATCH"}
    # Only the product hero image has an alt matching the product heading;
    # logos/recommendations and banners cannot be accidentally uploaded.
    images = [x["src"] for x in parser.images
              if _safe_image_url(x["src"], "dangapick", pid)
              and normalized_product_name(x["alt"]) ==
                  normalized_product_name(observed)]
    if len(set(images)) != 1:
        return {"provider": "dangapick", "status": "DETAIL_IMAGE_NOT_UNIQUE"}
    return _success("dangapick", images[0], observed, pid, iid,
                    page, folder, source_id, opener)


def lookup_dangapick_photo(identity: dict, title: str, folder: Path,
                           source_id: int, opener=None) -> dict:
    """Prefer the exact triplet, otherwise locate same-product indexed options."""
    pid = identity.get("product_id")
    if identity.get("status") != "EXACT_ITEM_IDENTIFIED" or not str(pid).isdigit():
        return {"provider": "dangapick", "status": "PRODUCT_ID_MISSING"}
    iid, vid = identity.get("item_id"), identity.get("vendor_item_id")
    if isinstance(iid, str) and iid.isdigit() and isinstance(vid, str) and vid.isdigit():
        direct = _dangapick_detail(
            identity, {"item": iid, "vendor": vid, "title": title}, folder,
            source_id, opener)
        if direct.get("status") == "IMAGE_SAVED":
            return direct
    # This public index is ~13.6 MB (2026-11-04); only fetch on this last
    # fallback, never during a successful Lowchart/Couspi lookup.
    try:
        payload = json.loads(_fetch("https://dangapick.com/data/products.json",
                                   "dangapick.com", MAX_INDEX_BYTES, opener))
    except Exception as exc:
        return {"provider": "dangapick", "status": "INDEX_UNAVAILABLE",
                "error_type": type(exc).__name__}
    matches = _dangapick_rows(payload, str(pid), title)
    matches.sort(key=lambda x: (
        0 if str(x.get("item")) == iid else 1,
        0 if vid and str(x.get("vendor")) == vid else 1,
        str(x.get("key", ""))))
    for candidate in matches[:4]:
        result = _dangapick_detail(identity, candidate, folder, source_id, opener)
        if result.get("status") == "IMAGE_SAVED":
            return result
    return {"provider": "dangapick", "status": "NO_VERIFIED_IMAGE",
            "same_product_candidates": len(matches)}


def ordered_external_photo(identity: dict, title: str, folder: Path,
                           source_id: int, *, couspi=None, dangapick=None) -> dict:
    """Called only after a failed Lowchart image. No other fallback providers."""
    attempts = []
    for name, func in (("couspi", couspi or lookup_couspi_photo),
                       ("dangapick", dangapick or lookup_dangapick_photo)):
        try:
            result = func(identity, title, folder, source_id)
        except Exception as exc:
            result = {"status": "SOURCE_EXCEPTION",
                      "error_type": type(exc).__name__}
        attempts.append({"provider": name, "status": result.get("status"),
                         "http_status": result.get("http_status"),
                         "error_type": result.get("error_type")})
        if (result.get("status") == "IMAGE_SAVED"
                and result.get("image_provider_verified") is True
                and result.get("image_provider_product_id") ==
                    identity.get("product_id")):
            return {**result, "photo_provider_attempts": attempts}
    return {"status": "PHOTO_EVIDENCE_UNAVAILABLE",
            "photo_provider_attempts": attempts}
