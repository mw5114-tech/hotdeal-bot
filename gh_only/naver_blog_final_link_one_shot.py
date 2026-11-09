"""Owner-approved ONE formatted Naver Blog hotdeal layout test (no cron).

Always a visibly labeled formatting test. Shares Naver GitHub state for durable
single-use lock; never silently replays the post or changes Oracle/Daangn code.
"""
from __future__ import annotations

import json
import os

from gh_only.naver_blog_latest_manual import latest_coupang, manual_source
from gh_only.naver_blog_jirum import Store, DRAFT_DIR, make_article, now_utc
from gh_only.pipeline import fetch_public_html, enrich_posts

STATE_KEY = "final_link_test_once_20261109"
SUFFIX = " [최종 링크 테스트]"


def run() -> int:
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_REF") != "refs/heads/main"
            or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
            or os.getenv("NAVER_FINAL_LINK_TEST_ONE_SHOT") != "true"):
        raise RuntimeError("NAVER_FINAL_LINK_TEST_NOT_AUTHORIZED")
    if os.getenv("COUPANG_PARTNER_SUBID") != "naverblog":
        raise RuntimeError("NAVER_FINAL_LINK_TEST_SUBID_WRONG")

    store = Store()
    before, _ = store.read()
    if before.get(STATE_KEY):
        print("NAVER_FINAL_LINK_TEST_ALREADY_ATTEMPTED " + json.dumps({
            "status": before[STATE_KEY].get("status"),
            "source_id": before[STATE_KEY].get("source_id"),
            "post_url": before[STATE_KEY].get("post_url"),
        }, ensure_ascii=False))
        return 0
    if before.get("in_flight") or before.get("pending"):
        raise RuntimeError("NAVER_FINAL_LINK_TEST_QUEUE_BUSY")

    latest = latest_coupang(enrich_posts(fetch_public_html(), max_posts=100))
    if latest is None:
        raise RuntimeError("NAVER_FINAL_LINK_TEST_NO_COUPANG_SOURCE")
    source = manual_source(latest)
    article = make_article(
        source, DRAFT_DIR,
        tag=os.environ["COUPANG_PARTNER_LPTAG"],
        manual_test_replay=True,
    )
    article["title"] = article["title"][:150-len(SUFFIX)].rstrip() + SUFFIX
    print("NAVER_FINAL_LINK_TEST_READY " + json.dumps({
        "source_id": source["id"],
        "source_post_url": source["source_post_url"],
        "title_chars": len(article["title"]),
        "body_chars": len(article["body"]),
        "photo_ready": bool(article["image"]),
        "bold_label_blocks": sum(bool(x.get("bold_prefix")) for x in article["blocks"]),
        "discount_label": next((x["text"] for x in article["blocks"]
                                if x.get("kind") == "discount"), ""),
    }, ensure_ascii=False))

    current, sha = store.read()
    if current.get(STATE_KEY) or current.get("pending") or current.get("in_flight"):
        raise RuntimeError("NAVER_FINAL_LINK_TEST_STATE_CHANGED")
    current[STATE_KEY] = {
        "status": "REVIEW_REQUIRED",
        "source_id": source["id"],
        "claimed_at": now_utc(),
        "run_id": os.getenv("GITHUB_RUN_ID"),
    }
    store.write(current, sha, "state: claim single verified-hyperlink Naver Blog test")
    # No retries after this point, including if browser fails before the click.
    from gh_only.naver_blog_deal_publisher import submit_one
    url = submit_one(
        article, os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""), publish=True
    )
    latest_state, sha = store.read()
    if latest_state.get(STATE_KEY, {}).get("status") != "REVIEW_REQUIRED":
        raise RuntimeError("NAVER_FINAL_LINK_TEST_CLAIM_LOST")
    latest_state[STATE_KEY]["status"] = "PUBLISHED"
    latest_state[STATE_KEY]["post_url"] = url
    latest_state[STATE_KEY]["published_at"] = now_utc()
    store.write(latest_state, sha, "state: confirm single verified-hyperlink Naver Blog test")
    print("NAVER_FINAL_LINK_TEST_PUBLISHED " + json.dumps({
        "source_id": source["id"], "post_url": url,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
