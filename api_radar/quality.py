"""Exact-item gate for API-sourced candidates.

All network requests are bounded, host-allowlisted and never print URL/query
tokens. A productId alone never establishes the exact offer/option, even if
its historical price appears to fall.
"""
from __future__ import annotations

from pathlib import Path
import re
from urllib import error, parse, request

from api_radar.collector import safe_url

MAX_HOPS = 4
MAX_IMG_BYTES = 2_500_000
COMMON = {"무료", "배송", "쿠팡", "정품", "특가", "행사", "공식", "옵션", "국내", "제품", "선택"}
TRACKING_HOSTS = {"link.coupang.com", "coupa.ng", "www.coupang.com", "m.coupang.com"}


class QualityError(RuntimeError):
    """Safe status code; no URL, cookies, source HTML or API values."""


def extract_exact_identity(url: str, expected_id: str) -> dict | None:
    if not safe_url(url) or not re.fullmatch(r"\d{5,18}", expected_id):
        return None
    u = parse.urlsplit(url)
    qs = parse.parse_qs(u.query)
    if u.hostname in ("www.coupang.com", "m.coupang.com"):
        matched = re.fullmatch(r"/vp/products/(\d{5,18})/?", u.path)
        product_id = matched.group(1) if matched else None
    elif u.hostname == "link.coupang.com" and u.path == "/re/AFFSDP":
        product_id = qs.get("pageKey", [None])[0]
    else:
        return None
    item_id = qs.get("itemId", [None])[0]
    vendor = qs.get("vendorItemId", [None])[0]
    if (product_id != expected_id or
            not isinstance(item_id, str) or not re.fullmatch(r"\d{5,18}", item_id)):
        return None
    if vendor is not None and not re.fullmatch(r"\d{5,18}", vendor):
        return None
    return {"product_id": product_id, "item_id": item_id,
            "vendor_item_id": vendor}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_exact_identity(product: dict, *, opener=None) -> dict:
    """Follow only known Coupang redirects, never third-party destinations."""
    pid = product.get("id")
    current = product.get("url")
    if not isinstance(pid, str) or not isinstance(current, str):
        raise QualityError("PRODUCT_ID_OR_LINK_MISSING")
    get = opener or request.build_opener(NoRedirect()).open
    for _ in range(MAX_HOPS):
        if not safe_url(current) or parse.urlsplit(current).hostname not in TRACKING_HOSTS:
            raise QualityError("UNTRUSTED_PRODUCT_REDIRECT")
        identity = extract_exact_identity(current, pid)
        if identity:
            # Contract with gh_only.direct_product_links.regular_product_url:
            # this status is earned ONLY after the allowlisted HTTPS URL
            # proves that both the productId and exact itemId match.
            # Do not grant it to an opaque shortlink or a productId-only URL.
            return {**identity, "status": "EXACT_ITEM_IDENTIFIED"}
        # An opaque affiliate tracking URL may count every automated GET as
        # a click. Never visit it merely to discover an option/item identity.
        if parse.urlsplit(current).hostname in ("link.coupang.com", "coupa.ng"):
            raise QualityError("OPAQUE_TRACKING_LINK_NOT_VISITED")
        try:
            response = get(request.Request(current, method="GET",
                         headers={"User-Agent": "AffiliateExactItemCheck/1.0"}),
                         timeout=8)
            with response:
                status = response.status
                location = response.headers.get("Location")
            if status not in (301, 302, 303, 307, 308) or not location:
                raise QualityError("EXACT_ITEM_URL_NOT_OBSERVED")
        except error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise QualityError("PRODUCT_REDIRECT_HTTP_UNAVAILABLE") from None
            location = exc.headers.get("Location")
        except (error.URLError, TimeoutError, OSError):
            raise QualityError("PRODUCT_REDIRECT_NETWORK_ERROR") from None
        if not location:
            raise QualityError("PRODUCT_REDIRECT_LOCATION_MISSING")
        current = parse.urljoin(current, location)
    raise QualityError("EXACT_ITEM_REDIRECT_NOT_RESOLVED")


def title_tokens(name: str) -> tuple[str, ...]:
    parts = [p for p in re.findall(r"[가-힣A-Za-z0-9]{2,}", name)
             if p not in COMMON and not p.isdigit()]
    unique = list(dict.fromkeys(parts))
    return tuple(unique[:2])


def check_exact_history(product: dict, identity: dict, *, fetch=None) -> dict:
    """Verify exact-option Lowchart average and price, including B-grade differences."""
    from gh_only.lowchart_price_reference import fetch_one
    title = product.get("name", "")
    tokens = title_tokens(title)
    if len(tokens) < 2:
        raise QualityError("NOT_ENOUGH_MATCHING_TITLE_TOKENS")
    if identity.get("product_id") != product.get("id"):
        raise QualityError("HISTORY_PRODUCT_ID_CONFLICT")
    price = product.get("price")
    if type(price) is not int or price <= 0:
        raise QualityError("CURRENT_PRICE_INVALID")
    try:
        row = (fetch or fetch_one)(identity["product_id"], identity["item_id"], tokens, price)
    except (ValueError, OSError, TimeoutError, TypeError):
        raise QualityError("HISTORY_FETCH_FAILED") from None
    if (not isinstance(row, dict) or
            row.get("source_item_identifiers_present") is not True or
            row.get("title_option_matches") is not True or
            row.get("usable_historical_reference") is not True or
            row.get("visible_exact_item_prices_verified") is not True):
        # Preserve the underlying safe Lowchart code so an absent page,
        # option mismatch, network error and missing average are distinguishable.
        detail = row.get("reason") if isinstance(row, dict) else None
        if not isinstance(detail, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,60}", detail):
            detail = "UNKNOWN"
        status = row.get("http_status") if isinstance(row, dict) else None
        if type(status) is int and status in (403, 404, 429, 500, 502, 503):
            detail += "_HTTP_" + str(status)
        raise QualityError("EXACT_OPTION_HISTORY_UNVERIFIED_" + detail)
    observed_current = row.get("observed_current_krw")
    average = row.get("observed_average_krw")
    # A valid exact-option average is useful for honest B-grade comparisons
    # even when the product costs MORE than its historical average.
    if not (type(observed_current) is int and type(average) is int
            and 1000 <= observed_current <= 4_000_000 and average > 0):
        raise QualityError("HISTORY_AVERAGE_OR_CURRENT_INVALID")
    if abs(observed_current - price) / max(price, 1) > .02:
        raise QualityError("CURRENT_PRICE_MISMATCH")
    discount = round((average - price) * 100 / average, 1)
    # Reject extreme/likely wrong-option observations, but do not confuse
    # a genuine below-15% saving with an unavailable price comparison.
    if not -70 <= discount <= 70:
        raise QualityError("DISCOUNT_IMPLAUSIBLE")
    window = row.get("history_window", "")
    if not isinstance(window, str) or not (
        re.fullmatch(r"recent_(\d{1,2})_weeks", window)
        or window == "entire_tracked_history_unspecified_days"
    ):
        raise QualityError("HISTORY_WINDOW_UNVERIFIED")
    return {
        "reference_price": average,
        "observed_current": observed_current,
        "discount_pct": discount,
        "history_window": window,
        "reference_kind": "lowchart_public_history",
        "source_url": row.get("source_url"),
        "exact_product_item_verified": True,
    }


def download_verified_image(url: str, folder: Path, pid: str, *, opener=None) -> Path:
    if not safe_url(url, image=True) or not re.fullmatch(r"\d{5,18}", pid):
        raise QualityError("IMAGE_SOURCE_NOT_ALLOWED")
    req = request.Request(url, method="GET",
                          headers={"Accept": "image/*", "User-Agent": "AffiliateImage/1.0"})
    try:
        with (opener or request.build_opener(NoRedirect()).open)(req, timeout=12) as res:
            status = res.status
            final = res.geturl()
            mime = res.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            data = res.read(MAX_IMG_BYTES + 1)
    except (error.HTTPError, error.URLError, OSError, TimeoutError):
        raise QualityError("IMAGE_FETCH_FAILED") from None
    if status != 200 or final != url or not 1000 <= len(data) <= MAX_IMG_BYTES:
        raise QualityError("IMAGE_SIZE_OR_LOCATION_INVALID")
    valid = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not valid.get(mime, False):
        raise QualityError("IMAGE_CONTENT_INVALID")
    folder.mkdir(parents=True, exist_ok=True)
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[mime]
    target = folder / ("api_radar_" + pid + ext)
    target.write_bytes(data)
    return target
