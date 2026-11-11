"""Official Coupang *Partners* Deep Link API client for Naver hotdeal posts.

Only the URL explicitly returned by the API can become an affiliate URL.
No API keys, response bodies, or authorization headers enter public logs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from urllib import error, request
from urllib.parse import parse_qs, urlsplit

from gh_only.direct_product_links import regular_product_url

HOST = "https://api-gateway.coupang.com"
PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
SUBID = "naverblog"
_MAX_RESPONSE_BYTES = 16_384
_TOKEN = re.compile(r"[A-Za-z0-9_-]{5,100}\Z")


class CoupangPartnersError(RuntimeError):
    """Exception messages deliberately omit credentials, URL and response."""


def issued_link_url(value: object) -> bool:
    """Allow only known HTTPS Coupang Partners short-link formats."""
    if not isinstance(value, str) or not 15 <= len(value) <= 250:
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or parsed.query or parsed.fragment):
            return False
    except (ValueError, TypeError):
        return False
    if parsed.hostname == "link.coupang.com":
        return bool(parsed.path.startswith("/a/") and
                    _TOKEN.fullmatch(parsed.path[len("/a/"):]))
    if parsed.hostname == "coupa.ng":
        return bool(_TOKEN.fullmatch(parsed.path.lstrip("/")) and
                    parsed.path.count("/") == 1)
    return False


def _input_url_ok(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https"
                and parsed.hostname == "www.coupang.com"
                and parsed.username is None and parsed.password is None
                and parsed.port is None and not parsed.fragment
                and (parsed.path.startswith("/vp/products/")
                     or parsed.path == "/np/search"))
    except (TypeError, ValueError):
        return False


def _landing_matches_identity(url: str, identity: dict, *, subid: str = SUBID) -> bool:
    """Accept an exact product link or an *API-returned* Coupang tracking URL.

    The official deeplink response's landingUrl can be
    link.coupang.com/re/AFFSDP with pageKey + itemId + subid, *not* a product
    page. This is NOT an invitation to synthesize or publish AFFSDP ourselves.
    The separately validated shortenUrl from the same signed API response is
    the only URL returned for publishing.
    """
    try:
        parsed = urlsplit(url)
        qs = parse_qs(parsed.query)
        if (parsed.scheme != "https" or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or parsed.fragment):
            return False
        vendor = identity.get("vendor_item_id")
        if parsed.hostname in ("www.coupang.com", "m.coupang.com"):
            return (
                parsed.path == f"/vp/products/{identity['product_id']}"
                and qs.get("itemId") == [identity["item_id"]]
                and (not vendor or qs.get("vendorItemId") == [vendor])
            )
        if parsed.hostname == "link.coupang.com" and parsed.path == "/re/AFFSDP":
            return (
                qs.get("pageKey") == [identity["product_id"]]
                and qs.get("itemId") == [identity["item_id"]]
                and (not vendor or "vendorItemId" not in qs
                     or qs.get("vendorItemId") == [vendor])
                and ("subid" not in qs or qs.get("subid") == [subid])
            )
        return False
    except (KeyError, TypeError, ValueError):
        return False


def authorization(access: str, secret: str, signed_date: str) -> str:
    if (not access or not secret or len(access) > 300 or len(secret) > 300
            or any(x in access + secret for x in ("\r", "\n", ","))):
        raise CoupangPartnersError("COUPANG_API_CREDENTIALS_INVALID")
    if not re.fullmatch(r"\d{6}T\d{6}Z", signed_date):
        raise CoupangPartnersError("COUPANG_API_SIGNED_DATE_INVALID")
    signature = hmac.new(
        secret.encode("utf-8"),
        (signed_date + "POST" + PATH).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return ("CEA algorithm=HmacSHA256, access-key=" + access +
            ", signed-date=" + signed_date + ", signature=" + signature)


def _validate_response(payload: object, original: str,
                       identity: dict | None, *, subid: str = SUBID) -> str:
    if not isinstance(payload, dict) or str(payload.get("rCode")) != "0":
        raise CoupangPartnersError("COUPANG_API_NON_SUCCESS")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise CoupangPartnersError("COUPANG_API_RESULT_NOT_UNIQUE")
    first = data[0]
    if first.get("originalUrl") != original:
        raise CoupangPartnersError("COUPANG_API_ORIGINAL_URL_MISMATCH")
    issued = first.get("shortenUrl")
    if not issued_link_url(issued):
        raise CoupangPartnersError("COUPANG_API_ISSUED_URL_INVALID")
    landing = first.get("landingUrl")
    if identity is not None and landing is not None:
        if not isinstance(landing, str) or not _landing_matches_identity(landing, identity, subid=subid):
            raise CoupangPartnersError("COUPANG_API_LANDING_ITEM_MISMATCH")
    return issued


def request_deeplink(url: str, *, identity: dict | None = None,
                     subid: str = SUBID) -> str:
    """A single POST only; each approved channel gets its own SUBID."""
    if subid not in (SUBID, "daangncafe"):
        raise CoupangPartnersError("COUPANG_API_SUBID_NOT_ALLOWED")
    if not isinstance(url, str) or len(url) > 512 or not _input_url_ok(url):
        raise CoupangPartnersError("COUPANG_API_INPUT_URL_INVALID")
    access = os.getenv("COUPANG_PARTNERS_ACCESS_KEY", "").strip()
    secret = os.getenv("COUPANG_PARTNERS_SECRET_KEY", "").strip()
    if not access or not secret:
        raise CoupangPartnersError("COUPANG_API_KEYS_MISSING")
    signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    header = authorization(access, secret, signed_date)
    body = json.dumps({"coupangUrls": [url], "subId": subid},
                      separators=(",", ":")).encode("utf-8")
    req = request.Request(
        HOST + PATH, data=body, method="POST",
        headers={
            "Authorization": header,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "NaverJirumOfficialPartners/1.0",
        },
    )
    try:
        with request.urlopen(req, timeout=18) as response:
            if response.status != 200:
                raise CoupangPartnersError("COUPANG_API_HTTP_NOT_200")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        # Never log the response body; it can contain request metadata.
        raise CoupangPartnersError("COUPANG_API_HTTP_" + str(exc.code)) from None
    except (error.URLError, TimeoutError, OSError):
        raise CoupangPartnersError("COUPANG_API_NETWORK_FAILURE") from None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise CoupangPartnersError("COUPANG_API_RESPONSE_OVERSIZE")
    try:
        response = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise CoupangPartnersError("COUPANG_API_RESPONSE_NOT_JSON") from None
    return _validate_response(response, url, identity, subid=subid)


def create_product_deeplink(identity: dict, *, subid: str = SUBID) -> dict:
    """Issue an official link for a verified exact product/item/option."""
    try:
        ordinary = regular_product_url(identity)
    except (ValueError, TypeError):
        raise CoupangPartnersError("COUPANG_API_PRODUCT_IDENTITY_INVALID") from None
    issued = request_deeplink(ordinary, identity=identity, subid=subid)
    return {
        "status": "OFFICIAL_COUPANG_PARTNERS_API",
        "partners_link": issued,
        "product_id": identity["product_id"],
        "item_id": identity["item_id"],
        "vendor_item_id": identity.get("vendor_item_id"),
        "subid_requested": subid,
        "official_link_issued": True,
        "commission_attribution_verified": False,
    }


def main() -> int:
    """API-only probe, no blog visits, state updates, or post submission."""
    # Same query-page form as the official Partners Python sample; no personal
    # product is posted or added to an affiliate channel by this probe.
    sample = "https://www.coupang.com/np/search?q=test"
    link = request_deeplink(sample)
    host = urlsplit(link).hostname
    print("COUPANG_OFFICIAL_API_PROBE_OK host=" + str(host) +
          " subid_requested=" + SUBID + " no_blog_post=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
