"""Build an experimental Coupang AFFSDP URL from a verified source redirect.

This is NOT an officially issued Coupang Partners link. Its ability to
attribute commissions is UNVERIFIED. Never mark as issued or auto-publish.
No HTTP, login, cookie, API or network call occurs in this module.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

LPTAG_PATTERN = re.compile(r"AF[0-9]{5,12}\Z")
INTEGER_PATTERN = re.compile(r"[1-9][0-9]{0,17}\Z")


def _positive_id(value: object) -> bool:
    return isinstance(value, str) and bool(INTEGER_PATTERN.fullmatch(value))


def compose_affsdp_candidate(identity: dict, owner_tag: str) -> dict:
    """Compose a tentative outbound URL using ONLY observed product/item IDs.

    We only trust identity after the upstream Coupang URL one-hop resolver.
    vendorItemId is optional because real issued redirects may omit it;
    never guess this ID. Returns an explicit not-issued status on all paths.
    """
    result = {
        "status": "NOT_COMPOSED",
        "candidate_url": None,
        "official_link_issued": False,
        "commission_attribution_verified": False,
        "seller_option_independently_verified": False,
        "ready_for_automatic_publication": False,
    }
    if not isinstance(owner_tag, str) or not LPTAG_PATTERN.fullmatch(owner_tag):
        return {**result, "status": "INVALID_OWNER_LPTAG"}
    if not isinstance(identity, dict) or identity.get("status") != "EXACT_ITEM_IDENTIFIED":
        return {**result, "status": "SOURCE_PRODUCT_ITEM_NOT_IDENTIFIED"}

    product = identity.get("product_id")
    item = identity.get("item_id")
    vendor = identity.get("vendor_item_id")
    if not _positive_id(product) or not _positive_id(item):
        return {**result, "status": "INVALID_PRODUCT_ITEM_ID"}
    if vendor is not None and not _positive_id(vendor):
        return {**result, "status": "INVALID_VENDOR_ITEM_ID"}

    fields = [("lptag", owner_tag), ("pageKey", product), ("itemId", item)]
    if vendor:
        fields.append(("vendorItemId", vendor))
    url = "https://link.coupang.com/re/AFFSDP?" + urlencode(fields)
    return {
        **result,
        "status": "UNISSUED_AFFSDP_CANDIDATE",
        "candidate_url": url,
        "vendor_item_id_included": bool(vendor),
    }
