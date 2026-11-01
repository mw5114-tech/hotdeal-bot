"""Compare three owner-issued Coupang Partners quick-link redirect shapes.

Exactly one HEAD per link. No cookies, page GET, login, purchases, or
conversion. An affiliate shortlink may nevertheless register a click.
Avoid exposing raw redirect URLs or any incidental tracking identifiers.
"""
from __future__ import annotations
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_CODES = {
    "melon": "iirLbZ0tci",
    "innisfree": "iizvHijR7Y",
    "baby_snack": "iizDtqhTP2",
}
KNOWN_PARTNER_TAG = "AF9668915"
ALLOWED_HOSTS = {"link.coupang.com", "coupang.com", "www.coupang.com", "m.coupang.com"}

class NoFollow(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None

def probe(label: str, code: str) -> dict:
    result = {"label": label, "short_code_length": len(code),
              "short_code_first_3": code[:3], "short_code_last_3": code[-3:],
              "request_method": "HEAD", "officially_issued_by_owner": True}
    if not re.fullmatch(r"[A-Za-z0-9]{10}", code):
        return result | {"result": "INVALID_CODE_FORMAT"}
    source = "https://link.coupang.com/a/" + code
    try:
        response = build_opener(NoFollow).open(Request(source, method="HEAD"), timeout=18)
    except HTTPError as error:
        response = error
    except (URLError, OSError) as error:
        return result | {"result": "TRANSPORT_FAILURE", "error_type": type(error).__name__}
    with response:
        result["status"] = response.code
        location = response.headers.get("Location", "")
        if not location:
            return result | {"result": "NO_LOCATION"}
        p = urlsplit(location)
        result["destination_host"] = p.hostname
        if p.scheme != "https" or p.hostname not in ALLOWED_HOSTS:
            return result | {"result": "UNEXPECTED_DESTINATION"}
        fields = parse_qs(p.query, keep_blank_values=True)
        result.update({
            "result": "REDIRECT_METADATA_OBSERVED",
            "destination_path": p.path,
            "query_keys": sorted(fields),
            "nonempty_parameter_keys": sorted(k for k, values in fields.items() if any(values)),
            "sig_nonempty": any(fields.get("sig", [])),
            "token_nonempty": any(fields.get("token", [])),
            "lptag_present": "lptag" in fields,
            "lptag_matches_owner": fields.get("lptag") == [KNOWN_PARTNER_TAG],
            "product_id_present": bool(re.search(r"/(?:vp|vm)/products/\d+", p.path)),
            "affiliate_landing_route": p.path == "/re/AFFSDP",
            "has_traceid": "traceid" in fields,
            "has_subid": "subid" in fields,
            "has_item_id": "itemId" in fields,
            "has_vendor_item_id": "vendorItemId" in fields,
        })
        # Do not print raw destination / values; those contain tracking identifiers.
    return result

if __name__ == "__main__":
    rows = [probe(label, code) for label, code in SOURCE_CODES.items()]
    comparison = {
        "all_codes_have_same_length": len({r["short_code_length"] for r in rows}) == 1,
        "all_lptag_match_owner": all(r.get("lptag_matches_owner") is True for r in rows),
        "same_redirect_path": len({r.get("destination_path") for r in rows}) == 1,
        "observations": rows,
        "commission_attribution_from_hand_built_AFFSDP_proven": False,
        "short_code_generation_algorithm_recovered": False,
        "login_attempted": False, "sales_attempted": False, "get_requests": 0,
    }
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
