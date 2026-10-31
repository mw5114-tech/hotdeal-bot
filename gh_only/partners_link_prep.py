"""Prepare an exact Coupang PRODUCT URL for the user's own Partners quick-link form.

Never feed another publisher's affiliate shortlink directly into generation.
Inspect one public 302 redirect only; do not open Coupang product HTML.
Only include productId/itemId/vendorItemId and discard tracking parameters.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from gh_only.pipeline import identify_url
from gh_only.partner_api import canonical_product_url, safe_link
from gh_only.shortlink_probe import one_hop


def prepare_url(input_url: str, resolve=one_hop) -> dict:
    if not isinstance(input_url, str) or len(input_url) > 3000:
        raise ValueError("INVALID_SOURCE_URL")
    record = identify_url(input_url)
    kind = record["url_kind"]
    if kind == "coupang_shortlink":
        if not safe_link(input_url):
            raise ValueError("INVALID_ORIGINAL_AFFILIATE_SHORTLINK")
        hop = resolve(input_url)
        if hop.get("result") != "DIRECT_PRODUCT_ID_CANDIDATE":
            return {"status": "UNRESOLVED_SHORTLINK", "canonical_url": None}
        pid, iid, vid = hop.get("product_id"), hop.get("item_id"), hop.get("vendor_item_id")
    elif kind == "coupang_product":
        # A malformed or duplicated vendorItemId must not silently select a
        # different seller option. Reject instead of discarding it.
        query = parse_qs(urlsplit(input_url).query, keep_blank_values=True)
        if "vendorItemId" in query and (
            len(query["vendorItemId"]) != 1 or not query["vendorItemId"][0].isdigit()
        ):
            return {"status": "INVALID_VENDOR_ITEM_ID", "canonical_url": None}
        if "itemId" in query and (
            len(query["itemId"]) != 1 or not query["itemId"][0].isdigit()
        ):
            return {"status": "INVALID_ITEM_ID", "canonical_url": None}
        pid, iid, vid = (record.get("product_id"), record.get("item_id"),
                         record.get("vendor_item_id"))
    else:
        return {"status": "NOT_A_SUPPORTED_COUPANG_PRODUCT", "canonical_url": None}

    if not (isinstance(pid, str) and pid.isdecimal() and
            isinstance(iid, str) and iid.isdecimal() and
            (vid is None or (isinstance(vid, str) and vid.isdecimal()))):
        return {"status": "PRODUCT_ITEM_ID_REQUIRED", "canonical_url": None}

    query = f"itemId={iid}" + (f"&vendorItemId={vid}" if vid else "")
    url = canonical_product_url("https://www.coupang.com/vp/products/" + pid + "?" + query)
    return {
        "status": "INPUT_READY",
        "canonical_url": url,
        "product_id": pid, "item_id": iid, "vendor_item_id": vid,
        "original_affiliate_link_reused": False,
        "publisher_id_copied": False,
        "opened_protected_product_html": False,
        "personal_partner_link_generated": False,
    }


def main():
    examples = {
        "whikare": "https://link.coupang.com/a/iiPBYpVHae",
        "hotcake": "https://link.coupang.com/a/iiCmQxWdUG",
    }
    items = {name: prepare_url(url) for name, url in examples.items()}
    print("COUPANG_PARTNERS_INPUT_PREP " + json.dumps(items, ensure_ascii=False))


if __name__ == "__main__":
    main()
