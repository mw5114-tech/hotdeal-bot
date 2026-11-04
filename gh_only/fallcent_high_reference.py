"""Public, exact-item Fallcent historical-high reference.

Read ONLY the visible, explicitly labelled price on the exact productId/itemId
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


def parse_visible_high(html: str, requested_title: str) -> dict:
    """Parse a single visible, labelled high; exclude disclaimers and examples."""
    out = {"status": "FALLCENT_HIGH_NOT_VISIBLE", "observed_high_krw": None}
    if not isinstance(html, str) or not isinstance(requested_title, str):
        return {**out, "status": "INVALID_PRICE_SOURCE_INPUT"}
    parser = VisibleText()
    parser.feed(html)
    heading = " ".join(parser.headings).strip()
    if not heading or not _same_product_family(requested_title, heading):
        return {**out, "status": "FALLCENT_PRODUCT_TITLE_MISMATCH",
                "source_title": heading[:180]}
    text = " ".join(parser.fragments)
    matches = [int(m.group(1).replace(",", "")) for m in PRICE_HIGH_RE.finditer(text)]
    matches = [v for v in matches if 100 <= v <= 100_000_000]
    if not matches:
        return {**out, "source_title": heading[:180]}
    if len(set(matches)) != 1:
        return {**out, "status": "FALLCENT_HIGH_AMBIGUOUS",
                "source_title": heading[:180]}
    return {"status": "HIGH_FOUND", "observed_high_krw": matches[0],
            "source_title": heading[:180],
            "price_basis": "fallcent_public_visible_historical_high"}


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
    """Fetch public direct-ID page. A missing historical high never becomes one."""
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
