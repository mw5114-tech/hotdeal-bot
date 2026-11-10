"""Public, exact-item Fallcent historical-high reference.

Read ONLY visible, explicitly labelled prices on the exact productId/itemId
public detail page; never infer it from discounts, graph placeholders,
home-page examples or an unrelated search result. No login/private APIs.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib import request, error
from urllib.parse import urlencode, urlsplit
import re

from gh_only.default_deal_format import normalized_product_name

MAX_HTML = 1_400_000
HOSTS = frozenset(("fallcent.com", "www.fallcent.com"))
PRICE_HIGH_RE = re.compile(
    r"(?:역대급\s*최고가|역대\s*최고가|(?<![가-힣])최고가)"
    r"\s*[:：]?\s*((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,8}))\s*원\b"
)
# Only labelled, user-visible figures from the verified exact item detail.
# Do NOT infer hidden highs from an unlabeled 64% badge or sample cards.
PRICE_LOW_RE = re.compile(
    r"(?:역대급\s*최저가|역대\s*최저가|(?<![가-힣])최저가)"
    r"\s*[:：]?\s*((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,8}))\s*원\b"
)
PRICE_CURRENT_RE = re.compile(
    r"(?<![가-힣])현재\s*가\s*[:：]?\s*"
    r"((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,8}))\s*원\b"
)
# Some older public exact-item pages expose an explicit average, separately
# from current price and a displayed percentage. That is the appropriate
# historical comparison for the requested "평소 판매가" wording.
PRICE_AVERAGE_RE = re.compile(
    r"(?:평균\s*가|평균\s*가격)\s*[:：]?\s*"
    r"((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d{2,8}))\s*원\b"
)
# A Fallcent percent badge may use a different denominator or a different
# observation timestamp. Keep it for diagnosis only, not as a formula to
# invent a normal price or override Telegram's actual sale price.
DISPLAYED_DISCOUNT_RE = re.compile(
    r"할인율\s*[:：]?\s*[-−▼]?\s*([1-9]\d?)\s*%"
)
VALID_ID = re.compile(r"\d{5,18}")


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.headings = []
        self._ignored = 0
        self._heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self._ignored += 1
        elif tag == "h1" and not self._ignored:
            self._heading += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._ignored:
            self._ignored -= 1
        elif tag == "h1" and self._heading:
            self._heading -= 1

    def handle_data(self, data):
        if self._ignored or not data.strip():
            return
        value = " ".join(data.split())
        self.fragments.append(value)
        if self._heading:
            self.headings.append(value)


def _same_product_family(requested: str, heading: str) -> bool:
    a, b = normalized_product_name(requested), normalized_product_name(heading)
    return bool(len(a) >= 9 and len(b) >= 9 and
                (a.startswith(b) or b.startswith(a)))


def _single_visible_price(text: str, pattern: re.Pattern) -> tuple[int | None, bool]:
    """Return (price, ambiguous); ignore all non-visible HTML/script contents."""
    values = [int(m.group(1).replace(",", "")) for m in pattern.finditer(text)]
    values = [v for v in values if 100 <= v <= 100_000_000]
    return (values[0], False) if len(set(values)) == 1 and values else (
        None, len(set(values)) > 1
    )


def exact_item_title_compatible(requested: str, heading: str) -> bool:
    """Title-family check *after* exact product_id+item_id verification.

    Fallcent's H1 sometimes lists the product family without selected option
    (e.g., 'Apple clear case'), while the Telegram title specifies '17 Pro'.
    Accept a shorter H1 only if the family matches, and reject explicitly
    contradictory size/quantity tokens when both headings list them.
    """
    if not _same_product_family(requested, heading):
        return False
    from gh_only.default_deal_format import _option_specs
    requested_specs = set(_option_specs(requested))
    heading_specs = set(_option_specs(heading))
    if requested_specs and heading_specs and requested_specs.isdisjoint(heading_specs):
        return False
    return True


def parse_visible_high(html: str, requested_title: str) -> dict:
    """Only exact-item h1 and visibly labelled high/current/low, no inferred prices.

    Keeps legacy 'FALLCENT_HIGH_NOT_VISIBLE' status when only current/low
    appear. Those individually verified prices may still be a truthful
    comparison, but cannot be called a normal/list/historical-average price.
    """
    out = {"status": "FALLCENT_HIGH_NOT_VISIBLE",
           "observed_high_krw": None, "observed_current_krw": None,
           "observed_low_krw": None, "observed_average_krw": None,
           "displayed_discount_pct": None}
    if not isinstance(html, str) or not isinstance(requested_title, str):
        return {**out, "status": "INVALID_PRICE_SOURCE_INPUT"}
    parser = VisibleText()
    parser.feed(html)
    heading = " ".join(parser.headings).strip()
    if not heading or not exact_item_title_compatible(requested_title, heading):
        return {**out, "status": "FALLCENT_PRODUCT_TITLE_MISMATCH",
                "source_title": heading[:180]}
    text = " ".join(parser.fragments)
    high, bad_high = _single_visible_price(text, PRICE_HIGH_RE)
    current, bad_current = _single_visible_price(text, PRICE_CURRENT_RE)
    low, bad_low = _single_visible_price(text, PRICE_LOW_RE)
    average, bad_average = _single_visible_price(text, PRICE_AVERAGE_RE)
    display_rates = set(int(m.group(1)) for m in
                        DISPLAYED_DISCOUNT_RE.finditer(text))
    displayed_discount = next(iter(display_rates)) if len(display_rates) == 1 else None
    if bad_high or bad_current or bad_low or bad_average:
        return {**out, "status": (
            "FALLCENT_HIGH_AMBIGUOUS" if bad_high else "FALLCENT_PRICES_AMBIGUOUS"
        ), "source_title": heading[:180]}
    if (high is not None and low is not None and high < low
            or average is not None and high is not None and average > high
            or average is not None and low is not None and average < low):
        return {**out, "status": "FALLCENT_PRICES_INCONSISTENT",
                "source_title": heading[:180]}
    return {
        **out,
        "status": "HIGH_FOUND" if high is not None else "FALLCENT_HIGH_NOT_VISIBLE",
        "observed_high_krw": high,
        "observed_current_krw": current,
        "observed_low_krw": low,
        "observed_average_krw": average,
        "displayed_discount_pct": displayed_discount,
        "source_title": heading[:180],
        "average_basis": ("fallcent_public_visible_observed_average"
                          if average is not None else None),
        "price_basis": ("fallcent_public_visible_historical_high"
                        if high is not None else None),
        "current_basis": ("fallcent_public_visible_current"
                          if current is not None else None),
        "low_basis": ("fallcent_public_visible_historical_low"
                      if low is not None else None),
    }


class _FallcentRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlsplit(newurl)
        if (u.scheme != "https" or u.hostname not in HOSTS
                or not u.path.startswith("/product/")
                or u.username or u.password or u.port not in (None, 443)):
            raise error.HTTPError(req.full_url, code, "INVALID_FALLCENT_REDIRECT",
                                  headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_exact_fallcent_high(identity: dict, requested_title: str,
                             *, opener=None) -> dict:
    """Fetch public exact-ID detail; current/low never masquerade as a high."""
    out = {"status": "FALLCENT_HIGH_UNAVAILABLE", "observed_high_krw": None}
    if not isinstance(identity, dict):
        return {**out, "status": "INVALID_PRODUCT_IDENTITY"}
    pid, iid = identity.get("product_id"), identity.get("item_id")
    if (identity.get("status") != "EXACT_ITEM_IDENTIFIED"
            or not isinstance(pid, str) or not VALID_ID.fullmatch(pid)
            or not isinstance(iid, str) or not VALID_ID.fullmatch(iid)
            or not isinstance(requested_title, str) or not requested_title.strip()):
        return {**out, "status": "INVALID_PRODUCT_IDENTITY"}
    url = "https://www.fallcent.com/product/?" + urlencode(
        {"item_id": iid, "product_id": pid})
    op = opener or request.build_opener(_FallcentRedirect()).open
    req = request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; PublicDealReader/1.0)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    try:
        with op(req, timeout=13) as resp:
            final = urlsplit(resp.geturl())
            if (resp.status != 200 or final.scheme != "https"
                    or final.hostname not in HOSTS
                    or not final.path.startswith("/product/")
                    or final.username or final.password
                    or final.port not in (None, 443)):
                return {**out, "status": "FALLCENT_RESPONSE_INVALID"}
            raw = resp.read(MAX_HTML + 1)
            if not raw or len(raw) > MAX_HTML:
                return {**out, "status": "FALLCENT_BODY_INVALID"}
            final_url = resp.geturl()
    except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
        return {**out, "status": "FALLCENT_REQUEST_FAILED",
                "error_type": type(exc).__name__,
                "http_status": getattr(exc, "code", None)}
    parsed = parse_visible_high(raw.decode("utf-8", "replace"),
                                requested_title)
    return {**parsed, "product_id": pid, "item_id": iid,
            "source_url": final_url, "exact_item_query": url}
