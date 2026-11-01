"""One-hop public Coupang affiliate shortlink redirect diagnostics.

Uses exactly one GET against a recognized shortlink host, never follows the
redirect, never opens a product page, and does not alter/claim affiliate links.
"""
from __future__ import annotations
import argparse
import json
import re
from urllib import request, error
from urllib.parse import urljoin, urlsplit

from gh_only.pipeline import identify_url
from gh_only.partner_api import safe_link


class StopRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def one_hop(link: str, opener=None) -> dict:
    if not safe_link(link):
        raise ValueError("COUPANG_SHORTLINK_REQUIRED")
    req = request.Request(link, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (compatible; DealLinkDiagnostic/1.0)",
        "Accept": "text/html",
    })
    urlopen = opener or request.build_opener(StopRedirect()).open
    try:
        response = urlopen(req, timeout=20)
    except error.HTTPError as exc:
        response = exc
    code = getattr(response, "status", None) or getattr(response, "code", None)
    if not isinstance(code, int):
        return {"status": None, "next_host": None, "product_id": None,
                "item_id": None, "vendor_item_id": None, "result": "UNKNOWN"}
    # Deliberately don't read the body, to avoid scraping an access-denied page.
    headers = getattr(response, "headers", {})
    where = headers.get("Location") if 300 <= code < 400 else None
    if not where:
        return {"status": code, "next_host": None, "product_id": None,
                "item_id": None, "vendor_item_id": None,
                "result": "NO_REDIRECT_TARGET"}
    final = urljoin(link, where)
    p = urlsplit(final)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        return {"status": code, "next_host": None, "product_id": None,
                "item_id": None, "vendor_item_id": None, "result": "UNTRUSTED_REDIRECT"}
    if p.hostname not in ("www.coupang.com", "coupang.com", "link.coupang.com", "coupa.ng"):
        return {"status": code, "next_host": None, "product_id": None,
                "item_id": None, "vendor_item_id": None, "result": "NON_COUPANG_REDIRECT"}
    result = identify_url(final)
    return {"status": code, "next_host": p.hostname,
            "product_id": result["product_id"],
            "item_id": result["item_id"],
            "vendor_item_id": result["vendor_item_id"],
            "result": "DIRECT_PRODUCT_ID_CANDIDATE" if result["product_id"] else "ADDITIONAL_HOP_NEEDED",
            "exact_option_verified": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="shortlink_diagnostic.json")
    args = parser.parse_args()
    try:
        report = one_hop(args.url)
        from pathlib import Path
        Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("SHORTLINK_DIAGNOSTIC " + json.dumps(report))
    except Exception:
        print("SHORTLINK_DIAGNOSTIC_ERROR")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
