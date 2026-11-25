"""Official NAVER Image Search API fallback for a confirmed unlisted Lowchart item.

A search match is representative, NOT proof of a Coupang item ID or price.
Only download an allowlisted public product thumbnail with a matching title.
"""
from __future__ import annotations
import html
from io import BytesIO
import json
import os
import re
from pathlib import Path
from urllib import request, parse, error
from gh_only.shortlink_probe import StopRedirect
from gh_only.default_deal_format import normalized_product_name, _option_specs
from gh_only.lowchart_product_photo import is_coupang_cdn_url

API = "https://naverapihub.apigw.ntruss.com/search/v1/image"
SOURCE = "naver_api_matched_image"
BASIS = "NAVER_API_TITLE_OPTION_MATCH_REPRESENTATIVE"
HOSTS = frozenset(("shop-phinf.pstatic.net", "shop1.phinf.naver.net",
                   "shop2.phinf.naver.net", "thumbnail.10x10.co.kr",
                   "sitem.ssgcdn.com"))
MAX_BYTES = 2500000

def matching_title(source, result):
    if not isinstance(source, str) or not isinstance(result, str):
        return False
    if not 4 <= len(source.strip()) <= 200 or not 4 <= len(result) <= 300:
        return False
    result = html.unescape(re.sub(r"<[^>]*>", "", result)).strip()
    main = re.split(r"\s+[:|]\s+|\s+-\s+", result, maxsplit=1)[0]
    main = re.sub(r"^\[[^\]]+\]\s*", "", main)
    a, b = normalized_product_name(source), normalized_product_name(main)
    if min(len(a), len(b)) < 5:
        return False
    def specs(value):
        value = re.sub(r"(\d+)\s*(?:캔|입|팩|봉|병|매|개)", r"\1개", value)
        return sorted(_option_specs(value))
    if specs(source) != specs(main):
        return False
    if a == b:
        return True
    # Allow a seller suffix carrying the original brand, never an added option.
    first = source.split(maxsplit=1)
    if len(first) == 2 and normalized_product_name(first[1]) == b:
        suffix = result[len(main):]
        if first[0] in suffix:
            return True
    # A model code is highly informative, but accessories are not the product.
    model = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{5,}\b")
    codes = {x.upper() for x in model.findall(source)}
    other = {x.upper() for x in model.findall(main)}
    if not codes or codes != other or re.search(r"교체|호환|부품|액세서리|케이스|필터|블레이드|칼날|전용", main):
        return False
    words = set(re.findall(r"[가-힣]{2,}", source))
    return len(words & set(re.findall(r"[가-힣]{2,}", main))) >= 2

def safe_url(raw):
    if not isinstance(raw, str) or len(raw) > 2500:
        return None
    try:
        u = parse.urlsplit(raw)
        if u.username or u.password or u.fragment or u.port not in (None, 443):
            return None
        if u.scheme == "http" and u.hostname in ("shop1.phinf.naver.net", "shop2.phinf.naver.net"):
            raw = u._replace(scheme="https").geturl()
            u = parse.urlsplit(raw)
        if u.scheme != "https":
            return None
        if u.hostname in HOSTS and re.search(r"\.(?:jpe?g|png|webp)$", u.path, re.I):
            return raw
        if is_coupang_cdn_url(raw):
            return raw
    except ValueError:
        pass
    return None

def fetch_matched_photo(title: str, folder: Path, source_id: int, *, opener=None):
    out = {"status": "NAVER_API_NO_MATCH", "image_source": SOURCE,
           "image_match_basis": BASIS, "image_provider_verified": False}
    if not isinstance(title, str) or not 4 <= len(title.strip()) <= 200:
        return out
    key_id = os.getenv("NAVER_API_HUB_KEY_ID", "").strip()
    key = os.getenv("NAVER_API_HUB_KEY", "").strip()
    if not key_id or not key:
        return {**out, "status": "NAVER_API_KEYS_MISSING"}
    op = opener or request.build_opener(StopRedirect()).open
    url = API + "?" + parse.urlencode({"query": title, "display": 20, "sort": "sim", "format": "json"})
    req = request.Request(url, headers={
        "X-NCP-APIGW-API-KEY-ID": key_id,
        "X-NCP-APIGW-API-KEY": key, "Accept": "application/json"})
    try:
        with op(req, timeout=12) as res:
            if res.status != 200 or res.geturl() != url:
                return {**out, "status": "NAVER_API_INVALID_RESPONSE"}
            data = res.read(400001)
        if len(data) > 400000:
            return {**out, "status": "NAVER_API_OVERSIZE"}
        items = json.loads(data).get("items", [])
    except (OSError, ValueError, AttributeError) as exc:
        return {**out, "status": "NAVER_API_REQUEST_FAILED", "error_type": type(exc).__name__}
    if not isinstance(items, list):
        return out
    attempts = 0
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        name = html.unescape(re.sub(r"<[^>]*>", "", str(item.get("title", ""))))
        image = safe_url(item.get("link"))
        if not image or not matching_title(title, name):
            continue
        attempts += 1
        if attempts > 3:
            break
        try:
            # Credentials belong to the API only, never the image host.
            with op(request.Request(image, headers={"Accept": "image/*"}), timeout=10) as res:
                if res.status != 200 or res.geturl() != image:
                    continue
                mime = res.headers.get_content_type()
                raw = res.read(MAX_BYTES + 1)
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(mime)
            if ext is None or not 1000 <= len(raw) <= MAX_BYTES:
                continue
            from PIL import Image
            with Image.open(BytesIO(raw)) as im:
                if (im.format not in ("JPEG", "PNG", "WEBP") or min(im.size) < 200
                        or max(im.size) > 5000 or im.width * im.height > 16000000):
                    continue
                im.verify()
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"jirum_{int(source_id)}_naver_api_photo{ext}"
            (folder / filename).write_bytes(raw)
            return {**out, "status": "IMAGE_SAVED", "saved_name": filename,
                    "bytes": len(raw), "image_provider_title": name[:300],
                    "image_query_title": title, "representative_naver_api_image": True}
        except (OSError, ValueError, SyntaxError):
            continue
    return out

def eligible_draft(row):
    photo = row.get("photo") or {}
    title = (row.get("caption") or {}).get("product_name")
    query = row.get("naver_api_query_title")
    if not isinstance(title, str) or not title.strip() or not isinstance(query, str):
        return False
    return bool(
        row.get("image_source") == SOURCE
        and row.get("image_metadata_status") == "LOWCHART_CONFIRMED_UNLISTED_200"
        and row.get("image_http_status") == 200
        and row.get("representative_naver_api_image") is True
        and row.get("image_match_basis") == BASIS
        and row.get("product_name_source") == "telegram_product_title"
        and photo.get("image_source") == SOURCE
        and photo.get("status") == "IMAGE_SAVED"
        and matching_title(row.get("naver_api_query_title"), row.get("image_provider_title"))
        and title == query[:len(title)].rstrip()
        and photo.get("saved_name") in (
            f"jirum_{row.get('source_message_id')}_naver_api_photo.jpg",
            f"jirum_{row.get('source_message_id')}_naver_api_photo.png",
            f"jirum_{row.get('source_message_id')}_naver_api_photo.webp",
        )
    )
