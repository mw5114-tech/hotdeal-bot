"""Owner-authorized one-shot: publish the *latest* Coupang message from @jirum.

This file is not part of the recurring Oracle loop. It shares the Naver FIFO
lock/state and never replays a previously claimed or published Telegram ID.
A Telegram post up to 24 hours old is allowed for THIS manual test only and
is clearly labelled as a test with price freshness disclaimer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from gh_only import jirum_manual_queue as old
from gh_only.pipeline import enrich_posts, fetch_public_html
from gh_only.naver_blog_jirum import DRAFT_DIR, Store, make_article, now_utc


def latest_coupang(rows: list[dict]) -> dict | None:
    """Choose last Coupang message, not last eligible/fresh message."""
    eligible = [x for x in rows if x.get("url_kind") in old.COUPANG_KINDS]
    return max(eligible, key=lambda x: x["source_message_id"]) if eligible else None


def manual_source(article: dict) -> dict:
    """Preserve original message price and timestamp for review/audit."""
    mid = article.get("source_message_id")
    timestamp = old._parse_utc(article.get("posted_at_utc"))
    if type(mid) is not int or mid <= 0 or timestamp is None:
        raise RuntimeError("MANUAL_LATEST_SOURCE_ID_OR_TIME_MISSING")
    elapsed = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if not -300 <= elapsed <= 24 * 3600:
        raise RuntimeError("MANUAL_LATEST_OLDER_THAN_24H")
    if type(article.get("sale_price_krw")) is not int or article["sale_price_krw"] <= 0:
        raise RuntimeError("MANUAL_LATEST_PRICE_MISSING")
    if article.get("source_post_url") != f"https://t.me/jirum/{mid}":
        raise RuntimeError("MANUAL_LATEST_SOURCE_URL_MISMATCH")
    link = article.get("original_purchase_url")
    if not isinstance(link, str) or not link.startswith("https://link.coupang.com/"):
        raise RuntimeError("MANUAL_LATEST_COUPANG_URL_MISSING")
    return {
        "id": mid,
        "source_message_id": mid,
        "source_post_url": article["source_post_url"],
        "original_purchase_url": link,
        "source_text": article.get("source_text", "")[:2000],
        "title": article.get("title", "")[:180],
        "sale_price_krw": article["sale_price_krw"],
        "posted_at_utc": article["posted_at_utc"],
    }


def run_once() -> int:
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_REF") != "refs/heads/main"
            or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
            or os.getenv("NAVER_MANUAL_LATEST_ONE_SHOT") != "true"):
        raise RuntimeError("MANUAL_LATEST_NOT_EXPLICITLY_AUTHORIZED")
    tag = os.getenv("COUPANG_PARTNER_LPTAG", "")
    if not tag or os.getenv("COUPANG_PARTNER_SUBID") != "naverblog":
        raise RuntimeError("MANUAL_LATEST_OWNER_TAG_OR_SUBID_MISSING")

    store = Store()
    state, _ = store.read()
    if state["in_flight"]:
        raise RuntimeError("MANUAL_LATEST_PREVIOUS_SUBMIT_REVIEW_REQUIRED")
    if state["pending"]:
        raise RuntimeError("MANUAL_LATEST_PENDING_AUTOMATIC_QUEUE_REVIEW_FIRST")

    latest = latest_coupang(enrich_posts(fetch_public_html(), max_posts=100))
    if latest is None:
        print("MANUAL_LATEST_NO_COUPANG_MESSAGE")
        return 3
    source = manual_source(latest)
    mid = source["id"]
    print("MANUAL_LATEST_SELECTED " + json.dumps({
        "source_id": mid,
        "source_url": source["source_post_url"],
        "posted_at_utc": source["posted_at_utc"],
        "price_krw_from_telegram": source["sale_price_krw"],
    }, ensure_ascii=False))

    if str(mid) in state["published"]:
        print("MANUAL_LATEST_ALREADY_PUBLISHED " + json.dumps(
            {"source_id": mid, "post_url": state["published"][str(mid)].get("url")},
            ensure_ascii=False,
        ))
        return 0
    if str(mid) in state["skipped"]:
        raise RuntimeError("MANUAL_LATEST_PREVIOUSLY_SKIPPED_REVIEW_FIRST")

    # All enrichment, link validation and image downloads happen BEFORE claim.
    # On failure no submission has been attempted and normal auto-post remains
    # available for future new Telegram IDs.
    article = make_article(
        source, DRAFT_DIR, tag=tag, manual_test_replay=True
    )
    print("MANUAL_LATEST_ARTICLE_READY " + json.dumps({
        "source_id": mid,
        "title_chars": len(article["title"]),
        "body_chars": len(article["body"]),
        "local_photo_ready": bool(article["image"]),
        "subid": "naverblog",
        "manual_stale_price_notice": True,
    }, ensure_ascii=False))

    # Pre-publish checkpoint. Never retry on uncertain network response.
    newest, sha = store.read()
    if (newest["in_flight"] or newest["pending"]
            or str(mid) in newest["published"] or str(mid) in newest["skipped"]):
        raise RuntimeError("MANUAL_LATEST_STATE_CHANGED_NO_SUBMIT")
    newest["last_scanned_id"] = max(newest["last_scanned_id"], mid)
    newest["pending"].append(source)
    newest["in_flight"] = {
        "id": mid, "status": "REVIEW_REQUIRED",
        "run_id": os.getenv("GITHUB_RUN_ID", "unknown"), "at": now_utc(),
        "reason": "OWNER_AUTHORIZED_MANUAL_LATEST_ONE_SHOT"
    }
    store.write(newest, sha, "state: claim owner-authorized latest Telegram test")

    # Write attempt starts here. A failure leaves an in-flight review lock;
    # repeated GitHub clicks are prevented from posting the same message.
    from gh_only.naver_blog_deal_publisher import submit_one
    url = submit_one(article, os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""),
                     publish=True)

    committed, sha = store.read()
    if (not committed["in_flight"]
            or committed["in_flight"]["id"] != mid
            or not committed["pending"]
            or committed["pending"][0]["id"] != mid):
        raise RuntimeError("MANUAL_LATEST_CLAIM_LOST_NO_REPOST")
    committed["pending"].pop(0)
    committed["in_flight"] = None
    committed["published"][str(mid)] = {
        "url": url, "at": now_utc(),
        "manual_test_replay": True,
        "source_post_url": source["source_post_url"]
    }
    store.write(committed, sha, "state: verify one Naver Blog Telegram manual test published")
    print("MANUAL_LATEST_PUBLISHED " + json.dumps({
        "source_id": mid, "post_url": url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
