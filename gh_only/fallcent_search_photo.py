"""Read-only Fallcent product-search image fallback for one Coupang deal.

Uses public search result cards, not Fallcent's generic og:image, which may
belong to an unrelated first result. Exact source/item verification is NOT
claimed for the representative search photo. Does not scrape price, change
affiliate URLs, log in, or publish anything.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib import request
from urllib.parse import urlencode, urlsplit
import re

from gh_only.lowchart_product_photo import is_coupang_cdn_url
from gh_only.shortlink_probe import StopRedirect

SEARCH_HOST = "fallcent.com"
MAX_HTML = 1_500_000
MAX_IMAGE = 2_500_000
IMAGE_SOURCE = "fallcent_search_representative"
MATCH_BASIS = "UNVERIFIED_FALLCENT_SEARCH_RESULT"

_SPEC_RE = re.compile(r"(?<!\d)\d+(?:\.\d+)?\s*(?:kg|g|ml|l|gb|tb|개|팩|입|봉|병)\b", re.I)
_SIZE_RE = re.compile(r"(?<!\d)\d+(?:\.\d+)?\s*(?:kg|g|ml|l|gb|tb)\b", re.I)
_YEAR_RE = re.compile(r"\b(?:20)?(\d{2})년(?:산)?\b")
_WORD_RE = re.compile(r"[가-힣]{2,}|[a-zA-Z]{3,}")
_STOP_WORDS = frozenset(("판매자배송", "역대급", "역대급최저가",
                         "쿠팡", "특가", "최저가", "햇소금", "상품", "배송",
                         "정품", "할인", "증정", "기획", "국내배송"))


def _tokens(text: str) -> set[str]:
    without_nums = re.sub(r"\d+(?:\.\d+)?(?:kg|g|ml|l|gb|tb|개|팩|입|봉|병)\b", " ", text, flags=re.I)
    without_years = _YEAR_RE.sub(" ", without_nums)
    return {t.casefold() for t in _WORD_RE.findall(without_years)
            if t.casefold() not in _STOP_WORDS}


def _specs(text: str, regex=_SPEC_RE) -> set[str]:
    return {re.sub(r"\s+", "", t).lower() for t in regex.findall(text)}


class _FallcentSearch(HTMLParser):
    """Collect only picture + readable title inside a real product anchor."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "a":
            href = fields.get("href") or ""
            if href.startswith("https://fcnt.link/"):
                self._current = {"href": href, "words": [], "images": []}
            else:
                self._current = None
        elif tag == "img" and self._current is not None:
            src = fields.get("src") or fields.get("data-src") or ""
            if is_coupang_cdn_url(src):
                self._current["images"].append(src)

    def handle_data(self, text):
        if self._current is not None and text.strip():
            self._current["words"].append(text.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self.cards.append({
                "title": " ".join(self._current["words"])[:600],
                "image": next(iter(self._current["images"]), None),
                "href": self._current["href"],
            })
            self._current = None


def choose_representative_card(html: str, title: str) -> dict | None:
    """Best relevant visible search card, not the page's unrelated OG hero.

    Reject clearly different weights and years; matching a family/variant
    doesn't assert the exact Coupang item or selected option.
    """
    parser = _FallcentSearch()
    parser.feed(html)
    wants = _tokens(title)
    weights = _specs(title, _SIZE_RE)
    any_specs = _specs(title)
    year = _YEAR_RE.search(title)
    if not wants:
        return None
    matches = []
    for pos, card in enumerate(parser.cards):
        candidate = card["title"]
        if not card["image"]:
            continue
        seen = _tokens(candidate)
        common = wants & seen
        if len(common) < min(2, len(wants)):
            continue
        cweights = _specs(candidate, _SIZE_RE)
        if weights and cweights and weights.isdisjoint(cweights):
            continue
        # If a package count is present in both, permit another variant,
        # but explicit package-weight mismatches are excluded above.
        cyear = _YEAR_RE.search(candidate)
        if year and cyear and year.group(1) != cyear.group(1):
            continue
        # Prefer matching specific product words and option sizes over
        # unrelated "salt" or marketplace advertisements.
        overlap = len(common)
        spec_hits = len(any_specs & _specs(candidate))
        score = overlap * 10 + spec_hits * 5
        if score >= 20:
            matches.append((-score, pos, card))
    if not matches:
        return None
    matches.sort(key=lambda entry: (entry[0], entry[1]))
    return matches[0][2]


def _search_urls(title: str) -> list[str]:
    titles = [re.sub(r"\s+", " ", title.strip())]
    without_year = _YEAR_RE.sub(" ", title)
    without_year = re.sub(r"\s+", " ", without_year).strip(" ,")
    if without_year and without_year != titles[0]:
        titles.append(without_year)
    return ["https://fallcent.com/product/search/?" + urlencode({"keyword": q})
            for q in titles[:2]]


def fetch_fallcent_product_photo(title: str, folder: Path, source_id: int,
                                opener=None) -> dict:
    """Download one relevant Fallcent search-card image without API/login."""
    out = {
        "status": "FALLCENT_NO_MATCHING_IMAGE",
        "provider": "fallcent_search",
        "image_source": IMAGE_SOURCE,
        "image_match_basis": MATCH_BASIS,
        "image_provider_verified": False,
        "representative_fallcent_image": False,
    }
    if (not isinstance(title, str) or not 4 <= len(title.strip()) <= 200
            or type(source_id) is not int or source_id < 1):
        return {**out, "status": "FALLCENT_TITLE_OR_SOURCE_INVALID"}
    op = opener or request.build_opener(StopRedirect()).open
    selected = None
    used_search = None
    for url in _search_urls(title):
        req = request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PublicProductImage/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        try:
            with op(req, timeout=12) as resp:
                landed = urlsplit(resp.geturl())
                if (resp.status != 200 or resp.geturl() != url
                        or landed.scheme != "https" or landed.hostname != SEARCH_HOST
                        or landed.path.rstrip("/") != "/product/search"):
                    continue
                data = resp.read(MAX_HTML + 1)
        except Exception:
            continue
        if not 0 < len(data) <= MAX_HTML:
            continue
        selected = choose_representative_card(data.decode("utf-8", "replace"), title)
        if selected is not None:
            used_search = url
            break
    if selected is None:
        return out

    image_url = selected["image"]
    if not is_coupang_cdn_url(image_url):
        return {**out, "status": "FALLCENT_IMAGE_URL_INVALID"}
    req = request.Request(image_url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "image/*",
    })
    try:
        with op(req, timeout=18) as resp:
            if resp.status != 200 or resp.geturl() != image_url:
                return {**out, "status": "FALLCENT_IMAGE_HTTP_INVALID"}
            mime = resp.headers.get_content_type()
            blob = resp.read(MAX_IMAGE + 1)
    except Exception:
        return {**out, "status": "FALLCENT_IMAGE_DOWNLOAD_FAILED"}
    ext = {"image/jpeg": ".jpg", "image/png": ".png",
           "image/webp": ".webp"}.get(mime)
    valid = ((ext == ".jpg" and blob.startswith(b"\xff\xd8\xff"))
             or (ext == ".png" and blob.startswith(b"\x89PNG\r\n\x1a\n"))
             or (ext == ".webp" and blob.startswith(b"RIFF")
                 and blob[8:12] == b"WEBP"))
    if not valid or not 1000 <= len(blob) <= MAX_IMAGE:
        return {**out, "status": "FALLCENT_IMAGE_BYTES_INVALID"}
    folder.mkdir(parents=True, exist_ok=True)
    saved_name = f"jirum_{source_id}_fallcent_photo{ext}"
    (folder / saved_name).write_bytes(blob)
    return {
        **out, "status": "IMAGE_SAVED", "saved_name": saved_name,
        "bytes": len(blob), "representative_fallcent_image": True,
        "image_provider_title": selected["title"][:180],
        "image_provider_page": used_search,
        "image_provider_result": selected["href"],
    }
