"""Optional approved Coupang Partners deeplink adapter.

No networking or secrets are used in offline CI. Never use Wing seller API keys:
these are DIFFERENT from approved Coupang Partners affiliate API credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from urllib import request
from urllib.parse import parse_qs, urlencode, urlsplit

DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
API_HOST = "https://api-gateway.coupang.com"
SHORTLINK_PATH = re.compile(r"/(?:a|re)/[A-Za-z0-9_-]+/?")
PRODUCT_PATH = re.compile(r"/vp/products/(\d+)/?")


def canonical_product_url(value: str) -> str:
    """Strip another affiliate's attribution; preserve the exact supplied item IDs."""
    if not isinstance(value, str) or len(value) > 3000:
        raise ValueError("DIRECT_PRODUCT_URL_REQUIRED")
    u = urlsplit(value)
    match = PRODUCT_PATH.fullmatch(u.path)
    if (u.scheme != "https" or u.hostname not in ("www.coupang.com", "coupang.com")
            or u.port not in (None, 443) or u.username or u.password or u.fragment or not match):
        raise ValueError("DIRECT_PRODUCT_URL_REQUIRED")
    query = parse_qs(u.query)
    item_ids = query.get("itemId", [])
    if len(item_ids) != 1 or not item_ids[0].isdigit():
        raise ValueError("ITEM_ID_REQUIRED_FOR_EXACT_OPTION")
    options = [("itemId", item_ids[0])]
    vendor_ids = query.get("vendorItemId", [])
    if vendor_ids:
        if len(vendor_ids) != 1 or not vendor_ids[0].isdigit():
            raise ValueError("INVALID_VENDOR_ITEM_ID")
        options.append(("vendorItemId", vendor_ids[0]))
    return "https://www.coupang.com/vp/products/" + match.group(1) + "?" + urlencode(options)


def sign(method: str, path: str, access_key: str, secret_key: str,
         timestamp: datetime | None = None) -> str:
    if not access_key or not secret_key:
        raise ValueError("APPROVED_PARTNERS_API_KEYS_REQUIRED")
    dt = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
    signed_date = dt.strftime("%y%m%dT%H%M%SZ")
    message = signed_date + method.upper() + path
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()
    return (f"CEA algorithm=HmacSHA256, access-key={access_key}, "
            f"signed-date={signed_date}, signature={digest}")


def safe_link(value: str) -> bool:
    if not isinstance(value, str) or len(value) > 400:
        return False
    p = urlsplit(value)
    return (p.scheme == "https" and p.hostname in ("link.coupang.com", "coupa.ng")
            and p.port in (None, 443) and not p.username and not p.password and
            not p.query and not p.fragment and
            bool(SHORTLINK_PATH.fullmatch(p.path) or
                 (p.hostname == "coupa.ng" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", p.path))))


def parse_deeplink_response(body: bytes | str, expected_url: str) -> str:
    obj = json.loads(body)
    if not isinstance(obj, dict) or str(obj.get("rCode")) != "0":
        raise RuntimeError("PARTNERS_API_NO_SUCCESS")
    results = obj.get("data")
    if not isinstance(results, list):
        raise RuntimeError("PARTNERS_API_INVALID_RESPONSE")
    matches = [r for r in results if isinstance(r, dict) and
               r.get("originalUrl") == expected_url and safe_link(r.get("shortenUrl"))]
    if len(matches) != 1:
        raise RuntimeError("PARTNERS_API_EXACT_LINK_UNCONFIRMED")
    return matches[0]["shortenUrl"]


def create_deeplink(direct_product_url: str, access_key: str, secret_key: str,
                    sub_id: str = "telegram1", opener=None) -> dict:
    """ONLY for approved account keys; does not verify live price or option rendering."""
    cleaned = canonical_product_url(direct_product_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sub_id):
        raise ValueError("INVALID_SUB_ID")
    payload = json.dumps({"coupangUrls": [cleaned], "subId": sub_id}).encode()
    auth = sign("POST", DEEPLINK_PATH, access_key, secret_key)
    req = request.Request(API_HOST + DEEPLINK_PATH, payload, method="POST", headers={
        "Authorization": auth, "Content-Type": "application/json"
    })
    open_fn = opener or request.urlopen
    with open_fn(req, timeout=20) as response:
        data = response.read(100_001)
    if len(data) > 100_000:
        raise RuntimeError("PARTNERS_API_RESPONSE_TOO_LARGE")
    generated = parse_deeplink_response(data, cleaned)
    return {"canonical_product_url": cleaned, "own_affiliate_url": generated,
            "source": "approved_coupang_partners_api",
            "exact_option_rendered_verified": False}
