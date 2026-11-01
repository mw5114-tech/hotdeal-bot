"""Research-only generator for Coupang product identity and a *hypothetical* direct link.

The /re/AFFSDP landing-address structure is publicly observable, but a
hand-constructed address is NOT a freshly issued Coupang Partners affiliate
link and is NOT proven to yield commissions. Never auto-post its output.

Input: official Coupang product URL or /re/AFFSDP URL with product/option IDs.
Opaque /a/ links require independently resolving the redirect first.
No HTTP calls, login, cookies, API, publishing or altered account tokens.
"""
from __future__ import annotations

import argparse
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

PRODUCT_PATH = re.compile(r"^/(?:vp|vm)/products/([0-9]+)/?$")
PARTNER_TAG = re.compile(r"^AF[0-9]{5,12}$")


def extract_product_identity(url: str) -> dict[str, str]:
    if not isinstance(url, str) or not 0 < len(url) <= 3000:
        raise ValueError("INVALID_SOURCE_URL")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.port not in (None, 443)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError("INVALID_SOURCE_URL")
    except ValueError:
        raise ValueError("INVALID_SOURCE_URL")
    host = (parsed.hostname or "").lower()
    if host == "link.coupang.com":
        if parsed.path != "/re/AFFSDP":
            raise ValueError("SHORTLINK_REQUIRES_OFFICIAL_REDIRECT")
        qs = parse_qs(parsed.query, keep_blank_values=True)
        product_id = _one_positive_integer(qs, "pageKey")
    elif host in ("coupang.com", "www.coupang.com", "m.coupang.com"):
        match = PRODUCT_PATH.fullmatch(parsed.path)
        if match is None:
            raise ValueError("NOT_PRODUCT_PAGE")
        product_id = match.group(1)
        qs = parse_qs(parsed.query, keep_blank_values=True)
    else:
        raise ValueError("UNRECOGNIZED_COUPANG_DOMAIN")
    item = _one_positive_integer(qs, "itemId")
    vendor = _one_positive_integer(qs, "vendorItemId")
    return {"productId": product_id, "itemId": item, "vendorItemId": vendor}


def _one_positive_integer(qs: dict, name: str) -> str:
    values = qs.get(name, [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit() or int(values[0]) <= 0:
        raise ValueError("MISSING_OR_AMBIGUOUS_" + name.upper())
    return values[0]


def product_url(identity: dict[str, str]) -> str:
    return (
        "https://www.coupang.com/vp/products/" + identity["productId"]
        + "?" + urlencode({"itemId": identity["itemId"], "vendorItemId": identity["vendorItemId"]})
    )


def hypothetical_landing_url(identity: dict[str, str], own_tag: str) -> str:
    """Just a URL format hypothesis; NEVER claim Coupang issued it or pays on it."""
    if not PARTNER_TAG.fullmatch(own_tag):
        raise ValueError("OWNER_TAG_FORMAT_INVALID")
    return "https://link.coupang.com/re/AFFSDP?" + urlencode({
        "lptag": own_tag,
        "pageKey": identity["productId"],
        "itemId": identity["itemId"],
        "vendorItemId": identity["vendorItemId"],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--own-lptag", help="Optional user-owned tag, for offline format study only")
    args = parser.parse_args()
    try:
        identity = extract_product_identity(args.source_url)
        result = {
            "status": "PRODUCT_IDENTITY_EXTRACTED",
            "product": identity,
            "official_product_url": product_url(identity),
            "new_affiliate_link_issued": False,
            "commission_attribution_verified": False,
            "safe_to_publish_as_affiliate": False,
        }
        if args.own_lptag:
            result["unverified_url_pattern_only"] = hypothetical_landing_url(identity, args.own_lptag)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as exc:
        print(json.dumps({"status": str(exc), "new_affiliate_link_issued": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
