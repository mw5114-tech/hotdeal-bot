"""Kakao Daum image search (official free quota) for Jirum photo candidates.

This module DOES NOT fetch Coupang product HTML or claim that image search
results prove seller/option identity or republication permissions.

No request will be sent without KAKAO_REST_API_KEY; tests inject fake JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import unescape
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib import request, error

ENDPOINT = "https://dapi.kakao.com/v2/search/image"
MAX_RESPONSE = 800_000
MAX_RESULTS = 30
MAX_QUERIES = 2


def safe_https(url: str | None) -> bool:
    if not isinstance(url, str) or len(url) > 2600:
        return False
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and bool(p.hostname) and not p.username
                and not p.password and p.port in (None, 443))
    except ValueError:
        return False


def url_product_identity(url: str, product_id: str, item_id: str) -> str:
    """Avoid credit for matching only the productId when itemId differs."""
    if not safe_https(url):
        return "UNUSABLE_SOURCE"
    p = urlsplit(url)
    if p.hostname not in {"www.coupang.com", "coupang.com"}:
        return "OTHER_SITE"
    if p.path.rstrip("/") != f"/vp/products/{product_id}":
        return "OTHER_COUPANG_PRODUCT"
    values = parse_qs(p.query)
    item = values.get("itemId")
    if item == [item_id]:
        return "SOURCE_URL_EXACT_ITEM"
    return "SOURCE_URL_PRODUCT_ONLY_OR_DIFFERENT_ITEM"


def useful_image(doc: dict) -> tuple[str | None, str | None]:
    """Accept HTTPS actual image URL only; keep thumbnail separate."""
    if not isinstance(doc, dict):
        return None, None
    original = doc.get("image_url")
    thumbnail = doc.get("thumbnail_url")
    return (original if safe_https(original) else None,
            thumbnail if safe_https(thumbnail) else None)


def normalize_search_results(payload: dict, product_id: str, item_id: str) -> dict:
    docs = payload.get("documents")
    if not isinstance(docs, list) or not isinstance(payload.get("meta"), dict):
        raise ValueError("BAD_KAKAO_IMAGE_SCHEMA")
    exact = []
    other = []
    for doc in docs[:MAX_RESULTS]:
        if not isinstance(doc, dict):
            continue
        original_url, thumbnail_url = useful_image(doc)
        image_url = original_url or thumbnail_url
        source = doc.get("doc_url")
        if not image_url or not safe_https(source):
            continue
        match = url_product_identity(source, product_id, item_id)
        width, height = doc.get("width"), doc.get("height")
        width = width if type(width) is int and 0 < width <= 20000 else None
        height = height if type(height) is int and 0 < height <= 20000 else None
        evidence = {
            "source_match": match, "image_url_candidate": image_url,
            "image_origin": "indexed_original" if original_url else "indexed_thumbnail_fallback",
            "thumbnail_url": thumbnail_url,
            "source_page_url": source,
            "source_site": doc.get("display_sitename"),
            "width": width, "height": height,
            "image_verified_for_exact_option": False,
            "permission_to_republish_verified": False,
        }
        if match == "SOURCE_URL_EXACT_ITEM":
            exact.append(evidence)
        else:
            other.append(evidence)
    return {
        "indexed_total_reported": payload["meta"].get("total_count"),
        "examined": min(len(docs), MAX_RESULTS),
        "exact_source_url_candidates": exact,
        "other_candidates": other[:5],
        "exact_item_image_content_verified": False,
        "safe_to_autopublish": False
    }


def query_terms(product_id: str, item_id: str, product_name: str) -> list[str]:
    if not re.fullmatch(r"[0-9]{5,18}", product_id) or not re.fullmatch(r"[0-9]{5,18}", item_id):
        raise ValueError("INVALID_PRODUCT_IDENTIFIERS")
    product_name = re.sub(r"\s+", " ", product_name).strip()
    if not 3 <= len(product_name) <= 110:
        raise ValueError("INVALID_PRODUCT_NAME")
    # Two queries total: one exact identity and one product description.
    return [f"{product_id} {item_id}", product_name]


def search_once(term: str, api_key: str, opener=None) -> dict:
    if not api_key or len(api_key) < 8:
        raise ValueError("KAKAO_REST_API_KEY_REQUIRED")
    if not isinstance(term, str) or len(term) > 160:
        raise ValueError("INVALID_SEARCH_TERM")
    url = ENDPOINT + "?" + urlencode({"query": term, "size": 30, "sort": "accuracy"})
    req = request.Request(url, headers={
        "Authorization": "KakaoAK " + api_key,
        "Accept": "application/json",
    })
    get = opener or request.urlopen
    try:
        with get(req, timeout=15) as response:
            code = response.status
            final = response.geturl()
            raw = response.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        # Do not expose error body or access key.
        return {"status": "API_HTTP_ERROR", "http_code": exc.code, "payload": None}
    if (code != 200 or urlsplit(final).hostname != "dapi.kakao.com"
            or len(raw) > MAX_RESPONSE):
        return {"status": "UNEXPECTED_API_RESPONSE", "http_code": code, "payload": None}
    return {"status": "OK", "http_code": 200, "payload": json.loads(raw)}


def find_image_candidates(product_id: str, item_id: str, product_name: str,
                          api_key: str, opener=None) -> dict:
    terms = query_terms(product_id, item_id, product_name)
    reports = []
    for term in terms[:MAX_QUERIES]:
        response = search_once(term, api_key, opener=opener)
        if response["status"] != "OK":
            reports.append({"query_kind": "exact_ids" if term == terms[0] else "product_name",
                            "status": response["status"],
                            "http_code": response.get("http_code"), "results": None})
            # On auth/rate failures, do not repeat.
            break
        findings = normalize_search_results(response["payload"], product_id, item_id)
        reports.append({"query_kind": "exact_ids" if term == terms[0] else "product_name",
                        "status": "OK", "http_code": 200, "results": findings})
        if findings["exact_source_url_candidates"]:
            break
    return {
        "schema": "jirum-kakao-photo-candidates.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_id": product_id, "item_id": item_id, "product_name": product_name,
        "requests_attempted": len(reports),
        "reports": reports,
        "exact_option_image_verified": False,
        "publish_ready": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", default="6221369038")
    parser.add_argument("--item-id", default="12453073752")
    parser.add_argument("--name", default="오뚜기 핫케이크 믹스 1kg 1개")
    parser.add_argument("--out", default="kakao_image_candidates.json")
    args = parser.parse_args()
    key = os.environ.get("KAKAO_REST_API_KEY", "")
    if not key:
        print("KAKAO_PHOTO_NOT_RUN: GitHub Secret KAKAO_REST_API_KEY not configured")
        return
    try:
        report = find_image_candidates(args.product_id, args.item_id, args.name, key)
    except Exception as exc:
        print("KAKAO_PHOTO_FAILED: " + type(exc).__name__)
        raise SystemExit(1)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print("KAKAO_PHOTO_RESULT " + json.dumps({
        "queries": report["requests_attempted"],
        "exact_url_candidates": sum(len(x["results"]["exact_source_url_candidates"])
                                    for x in report["reports"] if x["results"]),
        "other_candidates": sum(len(x["results"]["other_candidates"])
                                for x in report["reports"] if x["results"]),
        "published": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
