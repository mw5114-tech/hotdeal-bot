"""Single, read-only Goldbox-to-deeplink API integration probe.

Uses ONE Goldbox GET and ONE official Deeplink POST at most.
Does NOT open a browser, visit partner tracking links, save any state,
or submit/publish blog content. Never prints IDs, price, links or secrets.
"""
from __future__ import annotations
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from api_radar.collector import GOLDBOX, RadarApiError, get_api, extract_rows, normalize
from api_radar.quality import (
    QualityError, resolve_exact_identity, download_verified_image,
)
from api_radar.publisher import compose_goldbox_article
from gh_only.coupang_partners_api import (
    CoupangPartnersError, create_product_deeplink, issued_link_url,
)


class ProbeError(RuntimeError):
    pass


def run() -> int:
    feed = get_api(GOLDBOX)  # exactly one signed GET
    rows = extract_rows(feed)
    chosen = None
    product = None
    for raw in rows:
        normalized = normalize(raw, "goldbox")
        if normalized is None:
            continue
        try:
            identity = resolve_exact_identity(normalized)
        except QualityError:
            continue  # do not dereference opaque affiliate links
        if identity.get("status") != "EXACT_ITEM_IDENTIFIED":
            raise ProbeError("GOLDBOX_IDENTITY_CONTRACT_BROKEN")
        chosen = identity
        product = normalized
        break
    if chosen is None:
        raise ProbeError("GOLDBOX_EXACT_ITEM_NOT_AVAILABLE")
    issued = create_product_deeplink(chosen, subid="naverblog")  # 1 POST
    if (issued.get("status") != "OFFICIAL_COUPANG_PARTNERS_API"
            or issued.get("official_link_issued") is not True
            or not issued_link_url(issued.get("partners_link"))
            or issued.get("subid_requested") != "naverblog"):
        raise ProbeError("GOLDBOX_ISSUED_RESPONSE_INVALID")
    # Validate a complete B-grade article without entering SmartEditor,
    # creating a GitHub claim or submitting anything to Naver.
    with TemporaryDirectory(prefix="goldbox-preflight-") as temp:
        image = download_verified_image(product["image"], Path(temp), product["id"])
        article = compose_goldbox_article(product, image, issued["partners_link"])
        if (not article["title"].startswith("[쿠팡🎁골드박스] ")
                or article.get("grade") != "B"
                or article.get("link") != issued["partners_link"]
                or "할인율은 검증되지 않아 표시하지 않습니다" not in article["body"]):
            raise ProbeError("GOLDBOX_ARTICLE_STRUCTURE_INVALID")
    # Do not disclose URLs, lptag, product IDs or owner information.
    print("GOLDBOX_LIVE_B_ARTICLE_PREFLIGHT_OK "
          "verified_image=1 verified_blocks=1 naver_post=0")
    print("GOLDBOX_LIVE_DEEPLINK_OK "
          "source=official_goldbox subid=naverblog "
          "signed_get=1 signed_post=1 naver_post=0 browser=0 "
          "tracking_click=0 sensitive_fields_redacted=true")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (ProbeError, RadarApiError, CoupangPartnersError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,96}", code):
            code = "PROBE_UNKNOWN_SAFE_FAILURE"
        print("GOLDBOX_LIVE_DEEPLINK_STOP " + code)
        raise SystemExit(1) from None
