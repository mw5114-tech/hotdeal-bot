"""Second-choice product photo when Lowchart does not supply an image.

Google Images was independently tested on GitHub Actions and returned only a
CAPTCHA. Instead, this uses Naver's public image-search HTML, requiring both
the resolved Coupang *product ID* in the indexed source link AND matching
visible package/specification text. A search image is NOT independent proof
of Coupang itemId/vendorItemId; never say it is. No browser sessions, APIs,
paid services, redirects, or bypass of a search site's challenge.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib import request, error
from urllib.parse import urlencode, urlsplit

from gh_only.default_deal_format import (
    normalized_product_name, same_named_option,
)
from gh_only.lowchart_product_photo import (
    is_coupang_cdn_url, download_exact_photo,
)
from gh_only.shortlink_probe import StopRedirect

MAX_HTML_BYTES = 1_200_000
MAX_IMAGE_RESULTS = 80
MAX_QUERIES = 2
GOOGLE_UNAVAILABLE_REASON = "GOOGLE_IMAGES_CAPTCHA_ON_GITHUB_RUNNER"


def _coupang_product_id(link: str) -> str | None:
    if not isinstance(link, str):
        return None
    try:
        u = urlsplit(link)
    except ValueError:
        return None
    if (u.scheme != "https" or u.hostname not in ("coupang.com", "www.coupang.com")
            or u.username or u.password or u.port not in (None, 443)):
        return None
    found = re.fullmatch(r"/vp/products/(\d{5,18})/?", u.path)
    return found.group(1) if found else None


def _same_search_option(telegram_title: str, indexed_title: str) -> bool:
    # Ignore the indexed " - category | Coupang" suffix.
    a, b = normalized_product_name(telegram_title), normalized_product_name(indexed_title)
    if len(a) < 9 or len(b) < 9:
        return False
    # A longer product-family hit must preserve all explicit sizes/counts.
    return a == b or same_named_option(telegram_title, indexed_title)


def parse_naver_image_results(html: str, identity: dict, telegram_title: str) -> dict:
    """Parse only verified search-result records, never an arbitrary image tag."""
    out = {"status": "SEARCH_IMAGE_UNVERIFIED", "og_image": None,
           "image_source": "naver_indexed_coupang_product_image",
           "google_images_status": GOOGLE_UNAVAILABLE_REASON}
    pid = identity.get("product_id")
    if (identity.get("status") != "EXACT_ITEM_IDENTIFIED"
            or not isinstance(pid, str) or not re.fullmatch(r"\d{5,18}", pid)
            or not isinstance(telegram_title, str)):
        return {**out, "status": "SEARCH_PRODUCT_ID_UNVERIFIED"}
    found = re.search(r"\bitems:\s*(?=\[\{)", html)
    if not found:
        return {**out, "status": "SEARCH_RESULTS_MISSING"}
    try:
        rows, _ = json.JSONDecoder().raw_decode(html[found.end():])
    except (ValueError, TypeError):
        return {**out, "status": "SEARCH_RESULTS_MALFORMED"}
    if not isinstance(rows, list) or len(rows) > MAX_IMAGE_RESULTS:
        return {**out, "status": "SEARCH_RESULTS_TOO_MANY"}
    candidates = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        title, link, image = (row.get("title"), row.get("link"), row.get("originalUrl"))
        if (not isinstance(title, str) or not isinstance(image, str)
                or not is_coupang_cdn_url(image)
                or _coupang_product_id(link) != pid
                or not _same_search_option(telegram_title, title)):
            continue
        candidates[image] = {"title": title, "source_url": link}
    if not candidates:
        return {**out, "status": "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND"}
    if len(candidates) != 1:
        return {**out, "status": "SEARCH_PRODUCT_IMAGE_AMBIGUOUS",
                "candidate_count": len(candidates)}
    image, match = next(iter(candidates.items()))
    return {
        "status": "SEARCH_VERIFIED_COUPANG_PRODUCT_IMAGE",
        "og_title": re.split(r"\s+[-–]\s+", match["title"], maxsplit=1)[0].strip(),
        "og_url": match["source_url"], "og_image": image,
        "image_source": "naver_indexed_coupang_product_image",
        "image_match_basis": "EXACT_COUPANG_PRODUCT_ID_AND_MATCHING_OPTION_TITLE",
        "image_search_product_id_verified": True,
        "image_search_title_verified": True,
        "image_search_exact_item_id_verified": False,
        "google_images_status": GOOGLE_UNAVAILABLE_REASON,
    }


def lookup_search_product_photo(identity: dict, title: str, opener=None) -> dict:
    """Search two bounded public queries, selecting only an exact product match.

    Do not access a Coupang product page. Do not parse Naver's rendered
    thumbnail as the original; only return the indexed Coupang CDN URL.
    """
    pid = identity.get("product_id")
    if (identity.get("status") != "EXACT_ITEM_IDENTIFIED"
            or not isinstance(pid, str) or not isinstance(title, str)
            or not title.strip()):
        return {"status": "SEARCH_INPUT_NOT_VERIFIED", "og_image": None}
    open_fn = opener or request.build_opener(StopRedirect()).open
    last_status = "SEARCH_NO_MATCH"
    seen = set()
    for query in (f"쿠팡 {title}", f"쿠팡 {title} {pid}"):
        if query in seen:
            continue
        seen.add(query)
        url = "https://search.naver.com/search.naver?" + urlencode(
            {"where": "image", "query": query})
        req = request.Request(url, headers={
            "Accept": "text/html",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "User-Agent": "Mozilla/5.0 (compatible; ItemPhotoSource/1.0)",
        })
        try:
            with open_fn(req, timeout=13) as response:
                if (response.status != 200 or response.geturl() != url
                        or urlsplit(response.geturl()).hostname != "search.naver.com"):
                    last_status = "SEARCH_UNEXPECTED_RESPONSE"
                    continue
                body = response.read(MAX_HTML_BYTES + 1)
        except (error.HTTPError, error.URLError, TimeoutError):
            last_status = "SEARCH_REQUEST_FAILED"
            continue
        if len(body) > MAX_HTML_BYTES:
            last_status = "SEARCH_RESPONSE_TOO_LARGE"
            continue
        parsed = parse_naver_image_results(body.decode("utf-8", "replace"), identity, title)
        if parsed["status"] == "SEARCH_VERIFIED_COUPANG_PRODUCT_IMAGE":
            return parsed
        last_status = parsed["status"]
    return {"status": last_status, "og_image": None,
            "google_images_status": GOOGLE_UNAVAILABLE_REASON}


def download_search_photo(url: str | None, output: Path, source_message_id: int) -> dict:
    """Use existing Coupang CDN byte/MIME/signature checks, then label source."""
    result = download_exact_photo(url, output, source_message_id)
    if result.get("status") != "IMAGE_SAVED":
        return result
    prior = output / result["saved_name"]
    renamed = prior.with_name(prior.name.replace("_lowchart_photo", "_search_photo"))
    prior.replace(renamed)
    return {**result, "saved_name": renamed.name,
            "image_source": "naver_indexed_coupang_product_image"}
