"""Compare 6 user-owned issued short links with one matched unissued AFFSDP candidate.

Exact, bounded unauthenticated HEAD requests only. No GET, account session,
purchase, click simulation, link generation at Partners, or publishing.
An HTTP HEAD on a referral shortlink can still be logged as a click.
Print only first-party redirect structure, presence/absence of values, and
product/option numeric IDs. Do not reveal signed/tracking field values.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

CODES = (
    "ijnConur00",
    "ijnFXYw3NI",
    "ijnGPWuQgK",
    "ijnHmoeK3E",
    "ijnHXnt2US",
    "ijnIwt5dbo",
)
OWNER_TAG = "AF9668915"
TARGET_HOSTS = {"www.coupang.com", "coupang.com", "m.coupang.com"}
KNOWN_TRACK = (
    "lptag", "traceid", "sig", "token", "clickBeacon",
    "campaignid", "ctag", "subid", "offerId", "requestid",
    "itime", "addtag", "mcid", "src", "track",
)
MAX_REQUESTS = len(CODES) + 1


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def head_metadata(url: str) -> dict:
    # Redirect intentionally disabled. At most one request per supplied URL.
    try:
        r = build_opener(NoRedirect()).open(
            Request(url, method="HEAD", headers={"Accept": "text/html"}), timeout=17
        )
    except HTTPError as exc:
        r = exc
    except (URLError, OSError) as exc:
        return {"result": "NETWORK_ERROR", "error_type": type(exc).__name__}
    with r:
        status = r.code
        location = r.headers.get("Location")
    result = {"status": status, "has_location": bool(location)}
    if not location:
        return result
    dst = urlsplit(location)
    if dst.scheme != "https" or dst.hostname not in TARGET_HOSTS or dst.username or dst.password:
        return result | {"result": "DESTINATION_NOT_EXPECTED"}
    params = parse_qs(dst.query, keep_blank_values=True)
    match = re.fullmatch(r"/v[pm]/products/(\d+)", dst.path)
    identity = None
    if match:
        identity = {"productId": match.group(1)}
        for key in ("itemId", "vendorItemId"):
            values = params.get(key, [])
            if len(values) == 1 and values[0].isascii() and values[0].isdigit() and int(values[0]) > 0:
                identity[key] = values[0]
    return result | {
        "result": "FIRST_PARTY_REDIRECT",
        "destination_host": dst.hostname,
        "destination_path_kind": "product" if match else "other",
        "identity": identity,
        "owner_tag_matches": params.get("lptag") == [OWNER_TAG],
        "query_keys": sorted(params),
        "nonempty_keys": sorted(k for k, values in params.items() if any(values)),
        "tracked_fields": {
            k: {"present": k in params, "nonempty": bool(any(params.get(k, [])))}
            for k in KNOWN_TRACK
        },
    }


def same_product_candidate(identity: dict) -> str | None:
    # Official issued redirects in the six examples include productId and
    # itemId but no vendorItemId; don't invent missing vendor identifiers.
    if not identity or any(k not in identity for k in ("productId", "itemId")):
        return None
    return "https://link.coupang.com/re/AFFSDP?" + urlencode({
        "lptag": OWNER_TAG,
        "pageKey": identity["productId"],
        "itemId": identity["itemId"],
    })


def main():
    results = []
    for code in CODES:
        record = head_metadata("https://link.coupang.com/a/" + code)
        results.append({"code": code, "prefix3": code[:3], "code_length": len(code), **record})

    valid = [r for r in results if r.get("result") == "FIRST_PARTY_REDIRECT"]
    candidates = [r for r in valid if same_product_candidate(r.get("identity"))]
    candidate = {"result": "NOT_TESTED_NO_EXACT_MATCH_AVAILABLE"}
    if candidates:
        paired = candidates[0]
        candidate = {"paired_official_code": paired["code"], **head_metadata(same_product_candidate(paired["identity"]))}
        original_fields = paired.get("tracked_fields") or {}
        test_fields = candidate.get("tracked_fields") or {}
        candidate["field_nonempty_differences_from_official"] = [
            {"key": k, "official_nonempty": original_fields.get(k, {}).get("nonempty"),
             "candidate_nonempty": test_fields.get(k, {}).get("nonempty")}
            for k in KNOWN_TRACK
            if original_fields.get(k, {}).get("nonempty") != test_fields.get(k, {}).get("nonempty")
        ]
        candidate["observed_product_item_match"] = (
            isinstance(candidate.get("identity"), dict)
            and candidate["identity"].get("productId") == paired["identity"].get("productId")
            and candidate["identity"].get("itemId") == paired["identity"].get("itemId")
        )
        candidate["same_seller_confirmed"] = False

    summary = {
        "count_official_supplied": len(CODES),
        "official_http_302_count": sum(r.get("status") == 302 for r in results),
        "official_owner_tag_match_count": sum(r.get("owner_tag_matches") is True for r in results),
        "official_redirect_traceid_nonempty_count": sum(
            r.get("tracked_fields", {}).get("traceid", {}).get("nonempty") is True for r in results
        ),
        "official_redirect_sig_nonempty_count": sum(
            r.get("tracked_fields", {}).get("sig", {}).get("nonempty") is True for r in results
        ),
        "official_redirect_token_nonempty_count": sum(
            r.get("tracked_fields", {}).get("token", {}).get("nonempty") is True for r in results
        ),
        "official_short_code_lengths": sorted(set(len(x) for x in CODES)),
        "official_prefix3_count": dict(Counter(x[:3] for x in CODES)),
        "official_redirects": results,
        "matched_unissued_AFFSDP": candidate,
        "actual_official_link_issued_by_this_test": False,
        "commission_credit_or_payments_verified": False,
        "automatic_affiliate_publication_enabled": False,
        "http_methods": "HEAD only",
        "maximum_network_requests": MAX_REQUESTS,
        "same_seller_option_guaranteed": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
