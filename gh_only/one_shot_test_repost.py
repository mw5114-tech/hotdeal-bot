"""One explicit one-shot test: repost most recent already-published Jirum deal.

Does NOT delete/rewrite the regular FIFO history or change the watermark.
Uses a separate durable GitHub state claim, and never retries a possibly
submitted Daangn post. Invoked ONLY by a dedicated GitHub push-triggered job.
"""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import os
from urllib.parse import parse_qs, urlsplit

from gh_only.jirum_manual_queue import (
    GithubState, START_AFTER, build_next, collect_since, freshness_status,
    ingest, now_utc, validate_state,
)
from gh_only.official_link_receipts import parse_timestamp

REPOST_KEY = "single_test_repost_20261104"
EXPECTED_REPO = "mw5114-tech/hotdeal-bot"
EXPECTED_TRIGGER = "manual/trigger-repost-latest-20261103.txt"


def select_latest_published(state: dict) -> tuple[int, dict]:
    """Do not select a pending/unverified/old item or invent a source timestamp."""
    validate_state(state)
    candidates = [
        (int(key), value) for key, value in state["published"].items()
        if key.isdigit() and int(key) > START_AFTER
        and isinstance(value, dict)
        and value.get("status") in ("PUBLISHED_VERIFIED", "PUBLISHED_AWAITING_MUSE")
        and isinstance(value.get("post_url"), str)
        and value["post_url"].startswith("https://cafe.daangn.com/i-gagyeog-mos-c/posts/")
    ]
    if not candidates:
        raise RuntimeError("NO_PUBLISHED_ITEM_FOR_ONE_SHOT_TEST")
    # Most recently published source message, NOT most recently reedited post.
    mid, row = max(candidates, key=lambda item: item[0])
    if freshness_status(row) != "FRESH":
        raise RuntimeError("LATEST_PUBLISHED_SOURCE_EXPIRED_SKIP_TEST")
    if not row.get("product_id") or not row.get("item_id"):
        raise RuntimeError("LATEST_PUBLISHED_PRODUCT_IDS_NOT_RECORDED")
    return mid, row


def prepare_test_article(state: dict, source: dict, source_id: int, prior: dict):
    if source.get("source_message_id") != source_id:
        raise RuntimeError("TEST_REPOST_TELEGRAM_ID_MISMATCH")
    a = parse_timestamp(prior.get("posted_at_utc"))
    b = parse_timestamp(source.get("posted_at_utc"))
    if a is None or b is None or a != b:
        raise RuntimeError("TEST_REPOST_ORIGINAL_TIMESTAMP_MISMATCH")
    if freshness_status(source) != "FRESH":
        raise RuntimeError("TEST_REPOST_SOURCE_EXPIRED")
    if source.get("url_kind") not in ("coupang_shortlink", "coupang_product"):
        raise RuntimeError("TEST_REPOST_NOT_COUPANG")
    # The original published source ID is already in published; remove it in
    # a temporary copy only. Never persist this synthetic pending queue.
    temp = copy.deepcopy(state)
    temp["published"].pop(str(source_id))
    temp["pending"] = []
    temp["in_flight"] = None
    temp["last_scanned_id"] = source_id - 1
    temp, added, _ = ingest(temp, [source])
    if added != 1 or [row["id"] for row in temp["pending"]] != [source_id]:
        raise RuntimeError("TEST_REPOST_TEMP_QUEUE_INCORRECT")
    article = build_next(temp)
    if (article.get("source_id") != source_id
            or article.get("product_id") != prior["product_id"]
            or article.get("item_id") != prior["item_id"]
            or (prior.get("vendor_item_id") is not None
                and article.get("vendor_item_id") != prior["vendor_item_id"])
            or article.get("muse_rewrite_pending") is True):
        raise RuntimeError("TEST_REPOST_SOURCE_PRODUCT_MISMATCH")
    url = article.get("candidate_url", "")
    parsed = urlsplit(url)
    fields = parse_qs(parsed.query)
    if (parsed.scheme != "https" or parsed.hostname != "link.coupang.com"
            or parsed.path != "/re/AFFSDP"
            or fields.get("lptag") != ["AF9668915"]
            or fields.get("pageKey") != [prior["product_id"]]
            or fields.get("itemId") != [prior["item_id"]]):
        raise RuntimeError("TEST_REPOST_ORIGINAL_AFFSDP_NOT_RESTORED")
    if freshness_status(source) != "FRESH":
        raise RuntimeError("TEST_REPOST_EXPIRED_AFTER_BUILD")
    return article


def main():
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_REPOSITORY") != EXPECTED_REPO
            or os.getenv("GITHUB_REF") != "refs/heads/main"
            or os.getenv("GITHUB_EVENT_NAME") != "push"
            or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
            or os.getenv("JIRUM_IMAGE_SOURCE") != "lowchart"
            or os.getenv("JIRUM_REQUIRE_FRESH_POST") != "true"
            or os.getenv("JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK") != "false"
            or os.getenv("JIRUM_PUBLISH_BEFORE_MUSE_REWRITE") != "false"
            or os.getenv("COUPANG_PARTNER_LPTAG") != "AF9668915"):
        raise RuntimeError("ONE_SHOT_TEST_ENVIRONMENT_NOT_ALLOWED")
    from pathlib import Path
    if Path(EXPECTED_TRIGGER).read_text(encoding="utf-8").strip() != "RUN_ONCE_JIRUM_LATEST_REPOST_20261103":
        raise RuntimeError("ONE_SHOT_TEST_MARKER_INVALID")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        raise RuntimeError("ONE_SHOT_RUN_ID_MISSING")

    store = GithubState()
    state, _ = store.read()
    if REPOST_KEY in state:
        print("JIRUM_TEST_REPOST_ALREADY_ATTEMPTED " +
              json.dumps(state[REPOST_KEY], ensure_ascii=False))
        return 2
    if state["in_flight"] is not None or state["pending"]:
        raise RuntimeError("ONE_SHOT_TEST_NORMAL_QUEUE_NOT_IDLE")
    mid, prior = select_latest_published(state)
    # Locate ORIGINAL Telegram message instead of reusing a Daangn post's text.
    discovered = collect_since(mid - 1)
    source = next((d for d in discovered if d["source_message_id"] == mid), None)
    if source is None:
        raise RuntimeError("ONE_SHOT_ORIGINAL_TELEGRAM_POST_NOT_VISIBLE")
    article = prepare_test_article(state, source, mid, prior)

    # GitHub write is a pre-click durable checkpoint; an aborted or ambiguous
    # first attempt cannot accidentally cause a second submission.
    fresh_state, sha = store.read()
    if (REPOST_KEY in fresh_state or fresh_state["in_flight"] is not None
            or fresh_state["pending"] or str(mid) not in fresh_state["published"]):
        raise RuntimeError("ONE_SHOT_TEST_CHANGED_BEFORE_CLAIM")
    if freshness_status(source) != "FRESH":
        raise RuntimeError("ONE_SHOT_TEST_EXPIRED_BEFORE_CLAIM")
    fresh_state[REPOST_KEY] = {
        "status": "CLAIMED",
        "source_id": mid,
        "run_id": run_id,
        "original_post_url": prior["post_url"],
        "claimed_at": now_utc(),
    }
    store.write(fresh_state, sha, "test: claim one intentional latest Jirum repost")

    outcome = {"status": "NOT_STARTED", "submit_attempted": False}
    try:
        from gh_only.jirum_manual_publisher import publish_one
        outcome = publish_one(article)
    except Exception as exc:
        outcome = {
            "status": "PUBLISH_EXCEPTION_REVIEW",
            "error_type": type(exc).__name__,
            "submit_attempted": False,
        }

    after, latest_sha = store.read()
    claim = after.get(REPOST_KEY)
    if (not isinstance(claim, dict) or claim.get("source_id") != mid
            or claim.get("run_id") != run_id or claim.get("status") != "CLAIMED"):
        raise RuntimeError("ONE_SHOT_TEST_CLAIM_LOST_DO_NOT_RETRY")
    ok = (outcome.get("status") == "POST_PUBLISHED_AND_FORMAT_VERIFIED"
          and bool(outcome.get("post_url")))
    claim.update({
        "status": "PUBLISHED_TEST_VERIFIED" if ok else
                  ("PUBLISH_UNCERTAIN_REVIEW" if outcome.get("submit_attempted")
                   else "FAILED_BEFORE_SUBMIT"),
        "result": outcome.get("status"),
        "test_post_url": outcome.get("post_url"),
        "completed_at": now_utc(),
    })
    store.write(after, latest_sha, f"test: record single Jirum repost {mid}")
    print("JIRUM_ONE_SHOT_TEST_REPOST_RESULT " + json.dumps({
        "source_id": mid,
        "result": outcome.get("status"),
        "test_post_url": outcome.get("post_url"),
        "original_post_url": prior["post_url"],
        "submit_attempted": outcome.get("submit_attempted"),
        "status": claim["status"],
        "error_reason": outcome.get("error_reason"),
        "failure_stage": outcome.get("failure_stage"),
    }, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
