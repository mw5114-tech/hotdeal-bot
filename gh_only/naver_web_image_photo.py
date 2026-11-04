"""Retrieve the first ordinary Naver mobile image-search thumbnail, without API keys.

This is a representative search image, NOT evidence of the exact Coupang
item/option. The Telegram product title and independently verified Lowchart
price reference must not be replaced by the indexed image title or price.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib import request
from urllib.parse import urlencode, urlsplit
import re

from gh_only.shortlink_probe import StopRedirect

NAVER_HOST = "m.search.naver.com"
IMAGE_HOST = "search.pstatic.net"
MAX_HTML = 1_200_000
MAX_IMAGE = 2_500_000
MIN_IMAGE = 1000


class FirstImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._linked_images = []
        self._depth = 0
        self._seen_link = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            if self._seen_link:
                self._depth += 1
            elif "link_thumb" in (attrs.get("class") or "").split():
                self._seen_link = True
                self._depth = 1
        elif tag == "img" and self._seen_link:
            src = attrs.get("data-src") or attrs.get("src") or ""
            if src.startswith("https://") and urlsplit(src).hostname == IMAGE_HOST:
                self._linked_images.append((src, attrs.get("alt") or ""))

    def handle_endtag(self, tag):
        if tag == "a" and self._seen_link:
            self._depth -= 1
            if self._depth <= 0:
                self._seen_link = False
                self._depth = 0

    @property
    def first(self):
        return self._linked_images[0] if self._linked_images else None


def _safe_image_url(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 3000:
        return False
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and u.hostname == IMAGE_HOST and
                u.path == "/common/" and not u.username and not u.password and
                u.port in (None, 443) and
                u.query.startswith("src="))
    except ValueError:
        return False


def fetch_first_naver_photo(title: str, folder: Path, source_id: int,
                            opener=None) -> dict:
    """One mobile image-search request, one first-result download, no retries."""
    out = {"status": "NAVER_SEARCH_UNAVAILABLE", "provider": "naver_web",
           "image_source": "naver_web_first_image",
           "image_match_basis": "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT",
           "image_provider_verified": False}
    if not isinstance(title, str) or len(title.strip()) < 4 or len(title) > 200:
        return {**out, "status": "NAVER_TITLE_MISSING"}
    url = "https://m.search.naver.com/search.naver?" + urlencode(
        {"where": "m_image", "query": title.strip()})
    op = opener or request.build_opener(StopRedirect()).open
    req = request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/129.0.0.0 Mobile Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    try:
        with op(req, timeout=14) as response:
            if (response.status != 200 or response.geturl() != url or
                    urlsplit(response.geturl()).hostname != NAVER_HOST):
                return {**out, "status": "NAVER_SEARCH_RESPONSE_INVALID"}
            data = response.read(MAX_HTML + 1)
    except Exception as exc:
        return {**out, "status": "NAVER_SEARCH_REQUEST_FAILED",
                "error_type": type(exc).__name__}
    if len(data) > MAX_HTML:
        return {**out, "status": "NAVER_SEARCH_RESPONSE_TOO_LARGE"}
    parser = FirstImageParser()
    parser.feed(data.decode("utf-8", "replace"))
    first = parser.first
    if not first:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(locale="ko-KR")
                    response = page.goto(url, wait_until="domcontentloaded", timeout=16000)
                    if response and response.status == 200:
                        page.wait_for_timeout(1500)
                        rows = page.locator(".link_thumb img").evaluate_all(
                            """els => els.slice(0,5).map(x => ({
                              src:x.currentSrc || x.getAttribute('src') || '',
                              alt:x.getAttribute('alt') || '',
                              width:x.naturalWidth,height:x.naturalHeight
                            }))""")
                        for row in rows:
                            if (_safe_image_url(row["src"]) and row["width"] >= 100
                                    and row["height"] >= 100):
                                first = (row["src"], row["alt"])
                                break
                finally:
                    browser.close()
        except Exception as exc:
            return {**out, "status": "NAVER_BROWSER_REQUEST_FAILED",
                    "error_type": type(exc).__name__}
    if not first or not _safe_image_url(first[0]):
        return {**out, "status": "NAVER_FIRST_IMAGE_MISSING"}
    image_url, image_alt = first
    image_req = request.Request(image_url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "image/*",
    })
    try:
        with op(image_req, timeout=18) as response:
            if response.status != 200 or response.geturl() != image_url:
                return {**out, "status": "NAVER_IMAGE_REDIRECT_OR_STATUS"}
            mime = response.headers.get_content_type()
            content = response.read(MAX_IMAGE + 1)
    except Exception as exc:
        return {**out, "status": "NAVER_IMAGE_REQUEST_FAILED",
                "error_type": type(exc).__name__}
    ext = {"image/jpeg": ".jpg", "image/png": ".png",
           "image/webp": ".webp"}.get(mime)
    valid = ((ext == ".jpg" and content.startswith(b"\xff\xd8\xff"))
             or (ext == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
             or (ext == ".webp" and content.startswith(b"RIFF")
                 and content[8:12] == b"WEBP"))
    if not valid or not MIN_IMAGE <= len(content) <= MAX_IMAGE:
        return {**out, "status": "NAVER_IMAGE_BYTES_INVALID"}
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"jirum_{int(source_id)}_naver_web_photo{ext}"
    (folder / filename).write_bytes(content)
    return {
        **out, "status": "IMAGE_SAVED", "saved_name": filename,
        "bytes": len(content), "image_provider_title": image_alt[:180],
        "image_provider_page": url,
    }
