"""Bridge a verified Jirum photo/caption draft to a personal-link UI request.

This stage is safe on a GitHub-hosted runner because it never touches Partners.
The actual partner-owned URL is created later on an authenticated runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from gh_only.partner_api import canonical_product_url


def request_from_report(report: dict) -> dict:
    if not isinstance(report, dict) or report.get("schema") != "github-jirum-default-price-photo-v1":
        raise ValueError("UNRECOGNIZED_DRAFT_REPORT")
    drafts = report.get("drafts")
    if not isinstance(drafts, list) or len(drafts) != 1:
        raise ValueError("EXACTLY_ONE_DRAFT_REQUIRED")
    draft = drafts[0]
    if not isinstance(draft, dict) or draft.get("status") != "DRAFT_ONLY_DO_NOT_PUBLISH":
        raise ValueError("UNVERIFIED_DRAFT_STATUS")
    if draft.get("kakao_product_match") is not True:
        raise ValueError("OG_PRODUCT_ID_NOT_CONFIRMED")
    if draft.get("personal_affiliate_link_created") is not False:
        raise ValueError("AFFILIATE_LINK_ALREADY_CLAIMED")
    ident = draft.get("product_identity")
    if not isinstance(ident, dict) or ident.get("status") != "EXACT_ITEM_IDENTIFIED":
        raise ValueError("EXACT_ITEM_NOT_VERIFIED")
    pid, iid, vid = (ident.get("product_id"), ident.get("item_id"), ident.get("vendor_item_id"))
    if not (isinstance(pid, str) and pid.isdecimal() and
            isinstance(iid, str) and iid.isdecimal() and
            (vid is None or (isinstance(vid, str) and vid.isdecimal()))):
        raise ValueError("INCOMPLETE_PRODUCT_ID")
    msg_id = draft.get("source_message_id")
    if not isinstance(msg_id, int) or isinstance(msg_id, bool) or msg_id < 1:
        raise ValueError("INVALID_SOURCE_MESSAGE_ID")
    query = "?itemId=" + iid + ("&vendorItemId=" + vid if vid else "")
    url = canonical_product_url("https://www.coupang.com/vp/products/" + pid + query)
    request_id = hashlib.sha256((str(msg_id) + "\n" + url).encode()).hexdigest()[:20]
    return {
        "schema": "jirum-partners-one-link-request-v1",
        "request_id": request_id,
        "source_message_id": msg_id,
        "canonical_product_url": url,
        "status": "READY_FOR_AUTHENTICATED_PARTNERS_UI",
        "personal_affiliate_link_generated": False,
        "publish_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="partners-link-request.json")
    args = parser.parse_args()
    try:
        src = json.loads(Path(args.input).read_text(encoding="utf-8"))
        packet = request_from_report(src)
    except Exception as exc:
        print("DRAFT_HANDOFF_BLOCKED " + type(exc).__name__ + " " +
              str(exc) if isinstance(exc, ValueError) else
              "DRAFT_HANDOFF_BLOCKED_INVALID_REPORT")
        return 1
    Path(args.output).write_text(json.dumps(packet,ensure_ascii=False,indent=2)+"\n",
                                encoding="utf-8")
    print("DRAFT_HANDOFF_READY request_id=" + packet["request_id"] +
          " link_generated=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
