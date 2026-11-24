"""Lowchart-first image selection for trend comparison; Naver image API probe.

Do not infer item IDs or visit opaque affiliate tracking URLs. Search thumbnails
are diagnostic only; search indexing alone does not establish image-use rights
or exact product identity. No arbitrary remote hosts or secret-bearing redirects.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
from urllib import error, parse, request

from api_radar.quality import extract_exact_identity, NoRedirect
from gh_only.default_deal_format import normalized_product_name
from gh_only.lowchart_product_photo import (
    lookup_exact_item_photo, download_exact_photo, is_coupang_cdn_url,
)

HUB_IMAGE = "https://naverapihub.apigw.ntruss.com/search/v1/image"
MAX_IMAGE = 2_500_000
MAX_RESPONSE = 250_000


def exact_identity(product: dict, radar: dict) -> dict | None:
    pid = product.get("id")
    if not isinstance(pid, str):
        return None
    direct = extract_exact_identity(product.get("url"), pid)
    if direct:
        return {**direct, "status": "EXACT_ITEM_IDENTIFIED"}
    known = product.get("identity")
    if (isinstance(known, dict) and known.get("product_id") == pid
            and known.get("status") == "EXACT_ITEM_IDENTIFIED"
            and re.fullmatch(r"\d{5,18}", str(known.get("item_id", "")))):
        return known
    # The official SEARCH API sometimes returns a product-level opaque URL.
    # Reuse only an already observed exact-item URL of the SAME product ID
    # and matching product name, never invent a missing item ID.
    inventory = radar.get("goldbox_today") or {}
    row = (inventory.get("items") or {}).get(pid)
    history = (radar.get("products") or {}).get(pid)
    candidates = []
    if isinstance(row, dict):
        candidates.append((row.get("name"), row.get("url")))
    if isinstance(history, dict):
        candidates.append((history.get("name"), history.get("last_product_url")))
    name = normalized_product_name(product.get("name"))
    for known_name, url in candidates:
        if not name or normalized_product_name(known_name) != name:
            continue
        found = extract_exact_identity(url, pid)
        if found:
            return {**found, "status": "EXACT_ITEM_IDENTIFIED"}
    return None


def lowchart_photo(product: dict, radar: dict, folder: Path) -> dict:
    identity = exact_identity(product, radar)
    if identity is None:
        return {"status": "LOWCHART_EXACT_ITEM_ID_UNAVAILABLE"}
    info = lookup_exact_item_photo(identity, product["name"])
    if info.get("status") != "LOWCHART_EXACT_ITEM_IMAGE":
        status = info.get("status", "LOWCHART_IMAGE_NOT_FOUND")
        if type(info.get("http_status")) is int:
            status += "_HTTP_" + str(info["http_status"])
        return {"status": status}
    # An exact Lowchart page may have a different product-name/option label.
    # For a side-by-side comparison, don't show it as the chosen product
    # unless the existing title/option matcher agrees.
    if info.get("image_same_named_option") is not True:
        return {"status": "LOWCHART_COMPARISON_TITLE_MISMATCH"}
    folder.mkdir(parents=True, exist_ok=True)
    result = download_exact_photo(info["og_image"], folder, int(product["id"]))
    if result.get("status") == "IMAGE_SAVED":
        return {"status": "IMAGE_SAVED", "path": folder / result["saved_name"],
                "source": "lowchart_exact_item_public_page"}
    return {"status": result.get("status", "LOWCHART_DOWNLOAD_UNKNOWN")}


def safe_thumbnail(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 3000:
        return False
    try:
        u = parse.urlsplit(url)
        return (u.scheme == "https" and u.hostname == "search.pstatic.net"
                and u.port in (None, 443) and not u.username and not u.password
                and not u.fragment and u.path in ("/sunny/", "/common/")
                and bool(parse.parse_qs(u.query).get("src")))
    except ValueError:
        return False


def title_match(product: str, search_title: str) -> bool:
    cleaned = html.unescape(re.sub(r"</?b>", "", search_title, flags=re.I))
    a, b = normalized_product_name(product), normalized_product_name(cleaned)
    return len(a) >= 6 and a == b


def naver_image_search(title: str, *, opener=None) -> dict:
    kid = os.getenv("NAVER_API_HUB_KEY_ID", "").strip()
    key = os.getenv("NAVER_API_HUB_KEY", "").strip()
    if not kid or not key:
        return {"status": "NAVER_IMAGE_KEYS_MISSING", "items": []}
    if any(c in kid + key for c in "\r\n") or len(kid) > 256 or len(key) > 256:
        return {"status": "NAVER_IMAGE_KEYS_INVALID", "items": []}
    query = parse.urlencode({"query": title[:150], "display": 5,
                             "start": 1, "sort": "sim", "filter": "all",
                             "format": "json"})
    url = HUB_IMAGE + "?" + query
    req = request.Request(url, headers={
        "X-NCP-APIGW-API-KEY-ID": kid, "X-NCP-APIGW-API-KEY": key,
        "Accept": "application/json",
    })
    op = opener or request.build_opener(NoRedirect()).open
    try:
        with op(req, timeout=15) as res:
            if res.status != 200 or res.geturl() != url:
                return {"status": "NAVER_IMAGE_RESPONSE_UNEXPECTED", "items": []}
            raw = res.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        return {"status": "NAVER_IMAGE_HTTP_" + str(exc.code), "items": []}
    except (error.URLError, OSError, TimeoutError):
        return {"status": "NAVER_IMAGE_NETWORK_ERROR", "items": []}
    if len(raw) > MAX_RESPONSE:
        return {"status": "NAVER_IMAGE_RESPONSE_OVERSIZE", "items": []}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        return {"status": "NAVER_IMAGE_BAD_JSON", "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"status": "NAVER_IMAGE_SCHEMA_INVALID", "items": []}
    items = [i for i in data["items"][:5] if isinstance(i, dict)]
    return {"status": "NAVER_IMAGE_HTTP_200", "items": items}


def image_bytes(url: str, *, opener=None) -> dict:
    if not (safe_thumbnail(url) or is_coupang_cdn_url(url)):
        return {"status": "NAVER_IMAGE_HOST_REJECTED"}
    req = request.Request(url, headers={"Accept": "image/*", "User-Agent": "Mozilla/5.0"})
    op = opener or request.build_opener(NoRedirect()).open
    try:
        with op(req, timeout=15) as res:
            if res.status != 200 or res.geturl() != url:
                return {"status": "NAVER_IMAGE_DOWNLOAD_UNEXPECTED"}
            mime = res.headers.get_content_type()
            raw = res.read(MAX_IMAGE + 1)
    except error.HTTPError as exc:
        return {"status": "NAVER_IMAGE_DOWNLOAD_HTTP_" + str(exc.code)}
    except (error.URLError, OSError, TimeoutError):
        return {"status": "NAVER_IMAGE_DOWNLOAD_NETWORK_ERROR"}
    good = ((mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
            or (mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or (mime == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"))
    if not good or not 1000 <= len(raw) <= MAX_IMAGE:
        return {"status": "NAVER_IMAGE_INVALID_BYTES"}
    return {"status": "NAVER_IMAGE_BYTES_OK", "bytes": len(raw)}


def probe_naver_photo(product: dict) -> dict:
    """Test actual returned image bytes in memory, never publish a search hit."""
    result = naver_image_search(product["name"])
    items = result["items"]
    matching = [r for r in items if isinstance(r.get("title"), str)
                and title_match(product["name"], r["title"])]
    ordered = matching + [r for r in items if r not in matching]
    print("TREND_NAVER_IMAGE_API status=" + result["status"]
          + " results=" + str(len(items)) + " exact_title_hits=" + str(len(matching)))
    outcome = {"status": "NAVER_IMAGE_NO_DOWNLOADABLE_RESULT"}
    for item in ordered[:3]:
        # Only known provider hosts. No requests to arbitrary third-party URLs.
        urls = (item.get("thumbnail"), item.get("link"))
        for url in urls:
            if not (safe_thumbnail(url) or is_coupang_cdn_url(url)):
                continue
            outcome = image_bytes(url)
            if outcome["status"] == "NAVER_IMAGE_BYTES_OK":
                print("TREND_NAVER_IMAGE_DOWNLOAD status=NAVER_IMAGE_BYTES_OK"
                      + " bytes=" + str(outcome["bytes"])
                      + " exact_title=" + str(item in matching).lower()
                      + " approved_for_publish=false")
                return outcome
    print("TREND_NAVER_IMAGE_DOWNLOAD status=" + outcome["status"])
    return outcome


def choose_photo(a: dict, b: dict, radar: dict, folder: Path) -> dict:
    # Use only the existing Lowchart-verified photo method for live posting.
    # If both items are unavailable, exercise the official Naver API fallback
    # without claiming copyright permission or exact option verification.
    for label, product in (("A", a), ("B", b)):
        photo = lowchart_photo(product, radar, folder)
        print("TREND_LOWCHART_IMAGE product=" + label + " status=" + photo["status"])
        if photo["status"] == "IMAGE_SAVED":
            return {**photo, "label": label}
    probe_naver_photo(a)
    return {"status": "NO_PUBLISH_APPROVED_PRODUCT_IMAGE"}
