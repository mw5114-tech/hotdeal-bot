"""Generate an ordinary Coupang product URL for Muse to replace later.

This is NOT a Coupang Partners affiliate link. No lptag, /re/AFFSDP or
tracking parameters. The logged-in Muse browser must issue /a/ separately.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

_ID = re.compile(r"[1-9][0-9]{4,20}\Z")


def regular_product_url(ids: dict) -> str:
    if not isinstance(ids, dict) or ids.get("status") != "EXACT_ITEM_IDENTIFIED":
        raise ValueError("DIRECT_LINK_PRODUCT_UNVERIFIED")
    p, i = ids.get("product_id"), ids.get("item_id")
    vendor = ids.get("vendor_item_id")
    if (not isinstance(p, str) or not _ID.fullmatch(p)
            or not isinstance(i, str) or not _ID.fullmatch(i)
            or (vendor is not None and
                (not isinstance(vendor, str) or not _ID.fullmatch(vendor)))):
        raise ValueError("DIRECT_LINK_PRODUCT_IDS_INVALID")
    q = {"itemId": i}
    if vendor is not None:
        q["vendorItemId"] = vendor
    return f"https://www.coupang.com/vp/products/{p}?{urlencode(q)}"


def verify_regular_product_url(url: str, ids: dict) -> bool:
    if not isinstance(url, str) or len(url) > 300:
        return False
    try:
        value = urlsplit(url)
        q = parse_qs(value.query, strict_parsing=True)
    except ValueError:
        return False
    if (value.scheme != "https" or value.hostname != "www.coupang.com"
            or value.username is not None or value.password is not None
            or value.port is not None or value.fragment):
        return False
    if value.path != f"/vp/products/{ids.get('product_id')}":
        return False
    if q.get("itemId") != [ids.get("item_id")]:
        return False
    vendor = ids.get("vendor_item_id")
    return q == ({"itemId": [ids.get("item_id")],
                  **({"vendorItemId": [vendor]} if vendor else {})})
