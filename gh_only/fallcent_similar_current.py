"""Fallcent PUBLIC name-search displayed price, when exact Lowchart is unavailable.

This intentionally matches a PRODUCT FAMILY, not exact Coupang item/option.
The observed listing price may refer to a different quantity/variant.
Never label it as the featured item's normal price or compute a discount
against it. It is a sourced, independently labelled reference only.
No login, private API, third-party redirect, or product page fetching.
"""
from __future__ import annotations

from urllib import request, error
from urllib.parse import urlsplit, parse_qs
import re

from gh_only.fallcent_search_photo import (
    _FallcentSearch, _search_urls, _tokens, _SPEC_RE, _YEAR_RE,
    SEARCH_HOST, MAX_HTML,
)
from gh_only.shortlink_probe import StopRedirect

PRICE = re.compile(
    r"(?<![\d])([1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,8})\s*원(?![가-힣])"
)
MIN_PRICE, MAX_PRICE = 100, 100_000_000
STATUS = "FALLCENT_SIMILAR_SEARCH_CURRENT_VISIBLE"
# Prevent broad search-result matches driven only by marketing words.
GENERIC = frozenset(("남녀", "공용", "세트", "백팩", "가방", "대용량",
                      "추천", "여성", "남성", "키즈", "공식", "색상"))


def _displayed_price(card_text: str) -> int | None:
    """A SINGLE visible won price per card; reject original+discount pairs."""
    values = set()
    for m in PRICE.finditer(card_text):
        value = int(m.group(1).replace(",", ""))
        if MIN_PRICE <= value <= MAX_PRICE:
            values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def select_similar_current(html: str, query_title: str) -> dict | None:
    """Use strong family-name overlap, deliberately ignore package/option specs.

    Search-card link is provenance only; never visit fcnt.link or use its
    affiliate attribution. A close keyword match is NOT an exact SKU match.
    """
    if not isinstance(html, str) or not isinstance(query_title, str):
        return None
    parser = _FallcentSearch()
    parser.feed(html)
    requested = _tokens(query_title) - GENERIC
    if len(requested) < 2:
        return None
    ranked = []
    for position, card in enumerate(parser.cards):
        title = card.get("title") or ""
        price = _displayed_price(title)
        if price is None:
            continue
        candidate = _tokens(title) - GENERIC
        overlap = requested & candidate
        # 2 meaningful product-family words, or avoid unrelated results.
        if len(overlap) < 2:
            continue
        # A required year/weight/pack-option match is purposely NOT applied.
        # We still prefer stronger common model or brand terms.
        score = 10 * len(overlap) + 2 * len(overlap) / max(len(requested), 1)
        ranked.append((-score, position, title, card["href"], price, overlap))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (x[0], x[1]))
    _, _, title, result_url, price, overlap = ranked[0]
    return {
        "status": STATUS,
        "observed_current_krw": price,
        "source_title": title[:220],
        "search_match_basis": "SIMILAR_PRODUCT_FAMILY_ONLY_OPTION_UNCHECKED",
        "exact_item_verified": False,
        "displayed_price_provenance": "visible_single_price_on_fallcent_search_card",
        "common_terms": sorted(overlap),
        "result_url": result_url,
    }


def valid_search_price(proof: dict | None, query_title: str) -> int | None:
    """Cross-check all price/public-source fields before adding a blog label."""
    if not isinstance(proof, dict) or proof.get("status") != STATUS:
        return None
    price = proof.get("observed_current_krw")
    if (type(price) is not int or not MIN_PRICE <= price <= MAX_PRICE
            or proof.get("exact_item_verified") is not False
            or proof.get("search_match_basis") !=
               "SIMILAR_PRODUCT_FAMILY_ONLY_OPTION_UNCHECKED"
            or proof.get("displayed_price_provenance") !=
               "visible_single_price_on_fallcent_search_card"):
        return None
    result = urlsplit(proof.get("result_url") or "")
    source = urlsplit(proof.get("source_url") or "")
    if (result.scheme != "https" or result.hostname != "fcnt.link"
            or result.username or result.password or result.port not in (None, 443)
            or source.scheme != "https" or source.hostname != SEARCH_HOST
            or source.path.rstrip("/") != "/product/search"
            or source.username or source.password or source.port not in (None, 443)
            or set(parse_qs(source.query)) != {"keyword"}
            or not parse_qs(source.query)["keyword"][0].strip()):
        return None
    matches = proof.get("common_terms")
    requested = _tokens(query_title) - GENERIC
    candidate = _tokens(proof.get("source_title") or "") - GENERIC
    if (not isinstance(matches, list) or len(matches) < 2
            or set(matches) != (requested & candidate)
            or len(requested & candidate) < 2):
        return None
    if _displayed_price(proof["source_title"]) != price:
        return None
    return price


def fetch_similar_current(query_title: str, *, opener=None) -> dict:
    """Read at most two ordinary Fallcent public search pages; no redirects."""
    base = {
        "status": "FALLCENT_SIMILAR_SEARCH_NO_PRICE",
        "observed_current_krw": None,
        "exact_item_verified": False,
    }
    if not isinstance(query_title, str) or not 4 <= len(query_title.strip()) <= 200:
        return {**base, "status": "FALLCENT_SIMILAR_SEARCH_BAD_QUERY"}
    op = opener or request.build_opener(StopRedirect()).open
    for url in _search_urls(query_title)[:2]:
        req = request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PublicPriceReader/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        try:
            with op(req, timeout=10) as resp:
                parsed = urlsplit(resp.geturl())
                if (resp.status != 200 or resp.geturl() != url
                        or parsed.hostname != SEARCH_HOST
                        or parsed.scheme != "https"
                        or parsed.path.rstrip("/") != "/product/search"):
                    continue
                data = resp.read(MAX_HTML + 1)
                if not 0 < len(data) <= MAX_HTML:
                    continue
        except (error.HTTPError, error.URLError, OSError, TimeoutError):
            continue
        found = select_similar_current(data.decode("utf-8", "replace"),
                                       query_title)
        if found is None:
            continue
        out = {**found, "source_url": url}
        if valid_search_price(out, query_title) is not None:
            return out
    return base
