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
import time

from gh_only import jirum_manual_queue as old
from gh_only.pipeline import enrich_posts, fetch_public_html
from gh_only.naver_blog_jirum import (DRAFT_DIR, Store, make_article, now_utc,
                                       validate_state, NaverStateConflict)


def latest_coupang(rows: list[dict], *, target_id: int | None = None) -> dict | None:
    """Normally choose newest Coupang; optionally pin exactly one owner ID.

    Pinning avoids accidentally publishing a newer unrelated Telegram item
    when an owner requests one known deal by name/Telegram source ID.
    """
    if target_id is not None and (type(target_id) is not int or target_id <= 0):
        raise RuntimeError("MANUAL_TARGET_ID_INVALID")
    eligible = [x for x in rows if x.get("url_kind") in old.COUPANG_KINDS]
    if target_id is not None:
        matches = [x for x in eligible if x["source_message_id"] == target_id]
        if len(matches) != 1:
            raise RuntimeError("MANUAL_TARGET_NOT_FOUND_OR_AMBIGUOUS_NO_SUBMIT")
        return matches[0]
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


def claim_selected_latest(state: dict, source: dict, run_id: str) -> dict:
    """Claim latest without deleting/reordering older FIFO entries."""
    validate_state(state)
    mid = source["id"]
    if state["in_flight"] is not None:
        raise RuntimeError("MANUAL_LATEST_PREVIOUS_SUBMIT_REVIEW_REQUIRED")
    if str(mid) in state["published"] or str(mid) in state["skipped"]:
        raise RuntimeError("MANUAL_LATEST_ALREADY_HANDLED_NO_REPLAY")
    prior = next((r for r in state["pending"] if r["id"] == mid), None)
    if prior is not None:
        if any(prior.get(k) != source.get(k) for k in (
            "id", "source_post_url", "original_purchase_url",
            "sale_price_krw", "posted_at_utc"
        )):
            raise RuntimeError("MANUAL_LATEST_QUEUE_MISMATCH_NO_SUBMIT")
    else:
        if mid <= state["last_scanned_id"]:
            raise RuntimeError("MANUAL_LATEST_SCANNED_BUT_NOT_QUEUED_NO_REPLAY")
        state["pending"].append(source)
        state["pending"].sort(key=lambda row: row["id"])
        state["last_scanned_id"] = mid
    state["in_flight"] = {
        "id": mid, "status": "REVIEW_REQUIRED", "run_id": run_id,
        "at": now_utc(), "reason": "OWNER_AUTHORIZED_MANUAL_LATEST_ONE_SHOT"
    }
    validate_state(state)
    return state


def commit_selected_latest(state: dict, source: dict, url: str) -> dict:
    """Checkpoint only the manually selected ID after Naver verifies URL."""
    validate_state(state)
    mid = source["id"]
    if (state["in_flight"] is None
            or state["in_flight"].get("id") != mid
            or str(mid) in state["published"] or str(mid) in state["skipped"]):
        raise RuntimeError("MANUAL_LATEST_CLAIM_LOST_NO_REPOST")
    positions = [idx for idx, r in enumerate(state["pending"]) if r["id"] == mid]
    if len(positions) != 1:
        raise RuntimeError("MANUAL_LATEST_PENDING_NOT_FOUND_NO_REPOST")
    state["pending"].pop(positions[0])
    state["in_flight"] = None
    state["published"][str(mid)] = {
        "url": url, "at": now_utc(), "manual_test_replay": True,
        "source_post_url": source["source_post_url"]
    }
    validate_state(state)
    return state


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
    # Select the exact owner-requested Telegram ID, if one was supplied via
    # a protected GitHub trigger. A newer unrelated deal must not be posted.
    raw_target = os.getenv("NAVER_MANUAL_TARGET_ID", "").strip()
    if raw_target and (not raw_target.isdecimal() or len(raw_target) > 12):
        raise RuntimeError("MANUAL_TARGET_ID_INVALID")
    target_id = int(raw_target) if raw_target else None
    # The manual newest-item test preserves existing older FIFO entries.
    latest = latest_coupang(
        enrich_posts(fetch_public_html(), max_posts=100),
        target_id=target_id,
    )
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
    if (newest["in_flight"] or str(mid) in newest["published"]
            or str(mid) in newest["skipped"]):
        raise RuntimeError("MANUAL_LATEST_STATE_CHANGED_NO_SUBMIT")
    claim_selected_latest(newest, source, os.getenv("GITHUB_RUN_ID", "unknown"))
    store.write(newest, sha, "state: claim owner-authorized latest Telegram test")

    # Write attempt starts here. A failure leaves an in-flight review lock;
    # repeated GitHub clicks are prevented from posting the same message.
    from gh_only.naver_blog_deal_publisher import submit_one
    url = submit_one(article, os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""),
                     publish=True)

    # Read-after-write consistency on GitHub Contents can lag a successful
    # claim. Retry state reads and known 409 writes ONLY, never Naver submit.
    for attempt in range(12):
        committed, sha = store.read()
        prior = committed["published"].get(str(mid))
        if prior is not None:
            if prior.get("url") == url and committed["in_flight"] is None:
                break
            raise RuntimeError("MANUAL_LATEST_RECEIPT_CONFLICT_NO_REPOST")
        if committed["in_flight"] is None:
            if attempt == 11:
                raise RuntimeError("MANUAL_LATEST_CLAIM_NOT_VISIBLE_NO_REPOST")
            time.sleep(1)
            continue
        commit_selected_latest(committed, source, url)
        try:
            store.write(committed, sha,
                        "state: verify one Naver Blog Telegram manual test published")
            break
        except NaverStateConflict:
            if attempt == 11:
                raise
            time.sleep(1)
    else:
        raise RuntimeError("MANUAL_LATEST_CHECKPOINT_UNCONFIRMED_NO_REPOST")
    print("MANUAL_LATEST_PUBLISHED " + json.dumps({
        "source_id": mid, "post_url": url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_once())
