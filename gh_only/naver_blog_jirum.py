"""Independent Jirum -> Naver Blog FIFO using the existing Oracle-triggered Actions run.

Never changes the Daangn FIFO or Telegram worker. No hidden retries. A fresh
source timestamp, photo, product/item ID and owner's AFFSDP candidate are
required before any blog submission. Live submission is an explicit opt-in.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
import re
import time
from pathlib import Path
from urllib import error
from urllib.parse import parse_qs, urlsplit

from gh_only import jirum_manual_queue as old
from gh_only.default_deal_automation import enrich_deal
from gh_only.pipeline import fetch_public_html, enrich_posts

STATE_PATH = "state/naver_blog_jirum_fifo.json"
SCHEMA = "naver-blog-jirum-fifo-v1"
DRAFT_DIR = Path("naver-blog-one-draft")
MAX_PENDING = 40
MAX_HISTORY = 200
DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
NAVER_BLOG_SUBID = "naverblog"
NAVER_TITLE_MAX_UTF16_UNITS = 88
STALE_PUBLISH_CLAIM_SECONDS = 30 * 60
# One Oracle event can drain a bounded FIFO burst without another runner.
MAX_BATCH_POSTS = 4
BATCH_POST_GAP_SECONDS = 10
BATCH_MAX_ELAPSED_SECONDS = 10 * 60
BATCH_MIN_NEXT_POST_BUDGET_SECONDS = 120


def naver_title(title: str) -> str:
    """Fit long hotdeal headlines safely under SmartEditor title input limits.

    Preserve the [쿠팡🔥핫딜] prefix and trailing [NN%할인] where possible.
    Use UTF-16 code units since emoji occupy two units in browser editors.
    """
    prefix = "[쿠팡🔥핫딜] "
    if not isinstance(title, str) or not title.startswith(prefix):
        raise ValueError("NAVER_TITLE_PREFIX_INVALID")
    original = re.sub(r"\s+", " ", title).strip()
    match = re.search(r"( \[[0-9]{1,3}%할인\])$", original)
    suffix = match.group(1) if match else ""
    product = original[len(prefix):len(original) - len(suffix) if suffix else None]
    if not product.strip():
        raise ValueError("NAVER_TITLE_PRODUCT_MISSING")
    def units(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2
    if units(original) <= NAVER_TITLE_MAX_UTF16_UNITS:
        return original
    budget = NAVER_TITLE_MAX_UTF16_UNITS - units(prefix) - units(suffix) - 1
    if budget < 8:
        raise ValueError("NAVER_TITLE_INSUFFICIENT_BUDGET")
    out = ""
    for ch in product.strip():
        if units(out + ch) > budget:
            break
        out += ch
    shortened = (prefix + out.rstrip() + "…" + suffix).strip()
    if units(shortened) > NAVER_TITLE_MAX_UTF16_UNITS:
        raise ValueError("NAVER_TITLE_SIZE_EXCEEDED")
    return shortened


def archive_stale_claim(state: dict, *, reference: datetime | None = None) -> bool:
    """Quarantine a dead writer without replaying a potentially posted deal.

    A workflow may have clicked publish before its result was lost. A claim
    older than 30 minutes is no longer backed by a running 15-minute job.
    Put it in skipped rather than retrying or locking all future posts.
    """
    claim = state.get("in_flight")
    if claim is None:
        return False
    reference = reference or datetime.now(timezone.utc)
    claimed_at = old._parse_utc(claim.get("at"))
    if claimed_at is None:
        raise RuntimeError("NAVER_CLAIM_TIMESTAMP_MISSING_MANUAL_REVIEW")
    if (reference - claimed_at).total_seconds() < STALE_PUBLISH_CLAIM_SECONDS:
        return False
    if (not state.get("pending")
            or state["pending"][0].get("id") != claim.get("id")):
        raise RuntimeError("NAVER_CLAIM_QUEUE_INCONSISTENT")
    source = state["pending"].pop(0)
    state["skipped"][str(source["id"])] = {
        "reason": "PUBLISH_OUTCOME_UNCONFIRMED_NO_AUTORETRY",
        "claimed_run_id": claim.get("run_id"),
        "claimed_at": claim.get("at"),
        "source_post_url": source.get("source_post_url"),
        "at": reference.isoformat(),
    }
    state["in_flight"] = None
    validate_state(state)
    return True


def validate_naver_link_subid(url: str) -> None:
    """Prevent Daangn tracking labels leaking into Naver Blog posts."""
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if (parsed.scheme != "https"
            or parsed.hostname != "link.coupang.com"
            or parsed.path != "/re/AFFSDP"
            or query.get("subid") != [NAVER_BLOG_SUBID]):
        raise ValueError("NAVER_BLOG_SUBID_NOT_NAVERBLOG")



def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_state(s: dict) -> None:
    if not isinstance(s, dict) or s.get("schema") != SCHEMA:
        raise ValueError("NAVER_FIFO_INVALID_SCHEMA")
    h = s.get("last_scanned_id")
    if type(h) is not int or h <= 0:
        raise ValueError("NAVER_FIFO_BAD_CURSOR")
    pending = s.get("pending")
    if not isinstance(pending, list) or len(pending) > MAX_PENDING:
        raise ValueError("NAVER_FIFO_BAD_PENDING")
    ids = [x.get("id") for x in pending if isinstance(x, dict)]
    if len(ids) != len(pending) or ids != sorted(set(ids)) or any(
        type(x) is not int or x > h for x in ids
    ):
        raise ValueError("NAVER_FIFO_BAD_IDS")
    done, skipped = s.get("published"), s.get("skipped")
    if not isinstance(done, dict) or not isinstance(skipped, dict):
        raise ValueError("NAVER_FIFO_BAD_AUDIT")
    for id_ in ids:
        if str(id_) in done or str(id_) in skipped:
            raise ValueError("NAVER_FIFO_REPLAY_CONFLICT")
    claim = s.get("in_flight")
    if claim is not None and (not isinstance(claim, dict)
                              or claim.get("id") not in ids):
        raise ValueError("NAVER_FIFO_BAD_CLAIM")
    if len(json.dumps(s, ensure_ascii=False).encode("utf-8")) > 750_000:
        raise ValueError("NAVER_FIFO_TOO_LARGE")


class NaverStateConflict(RuntimeError):
    """Known GitHub Contents 409: no blind PUT retry; rebase the operation."""


class Store(old.GithubState):
    """Reuse the existing credential-safe GitHub Contents transport only."""
    def __init__(self):
        super().__init__()
        self.url = f"https://api.github.com/repos/{self.repo}/contents/{STATE_PATH}"

    def read(self):
        response = self.api("GET")
        blob = base64.b64decode(response["content"].replace("\n", ""))
        s = json.loads(blob.decode("utf-8"))
        validate_state(s)
        return s, response["sha"]

    def write(self, state: dict, sha: str, message: str):
        validate_state(state)
        raw = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        data = {"message": message + " [skip ci]", "content": base64.b64encode(
            raw.encode("utf-8")).decode("ascii"), "sha": sha, "branch": "main"}
        try:
            response = self.api("PUT", data)
        except error.HTTPError as exc:
            # For a confirmed 409, GitHub refused this stale commit. Read the
            # persisted state once; the CALLER may recompute an operation from
            # the latest SHA, but never replay this identical PUT.
            confirmed, _ = self.read()
            if confirmed == state:
                return
            if exc.code == 409:
                raise NaverStateConflict("NAVER_STATE_KNOWN_409_REBASE") from exc
            raise RuntimeError("NAVER_STATE_COMMIT_UNCERTAIN") from exc
        except (error.URLError, TimeoutError) as exc:
            # Transport errors may have committed despite a lost response.
            # Do not retry an ambiguous write.
            confirmed, _ = self.read()
            if confirmed == state:
                return
            raise RuntimeError("NAVER_STATE_COMMIT_UNCERTAIN") from exc
        if not response.get("commit", {}).get("sha"):
            raise RuntimeError("NAVER_STATE_COMMIT_UNCONFIRMED")


def source_fresh(row: dict, reference: datetime | None = None) -> bool:
    """Require Telegram's original timestamp, not GitHub scan time."""
    return old.freshness_status(row, reference=reference) == "FRESH"


def expire(s: dict, reference: datetime | None = None) -> int:
    keep = []
    count = 0
    for row in s["pending"]:
        if source_fresh(row, reference=reference):
            keep.append(row)
        else:
            s["skipped"][str(row["id"])] = {
                "reason": "STALE_OR_UNKNOWN_TIMESTAMP", "at": now_utc()
            }
            count += 1
    s["pending"] = keep
    return count


def ingest(s: dict, items: list[dict], reference: datetime | None = None):
    """One source ID maps to at most one queued article; never backfill history."""
    validate_state(s)
    added = 0
    for item in sorted(items, key=lambda x: x["source_message_id"]):
        mid = item["source_message_id"]
        if type(mid) is not int or mid <= s["last_scanned_id"]:
            continue
        if item.get("url_kind") not in old.COUPANG_KINDS:
            continue
        if type(item.get("sale_price_krw")) is not int:
            s["skipped"][str(mid)] = {"reason": "PRICE_NOT_FOUND", "at": now_utc()}
            continue
        row = {
            "id": mid,
            "source_message_id": mid,
            "source_post_url": item["source_post_url"],
            "original_purchase_url": item["original_purchase_url"],
            "title": item.get("title", "")[:180],
            "source_text": item.get("source_text", "")[:2000],
            "sale_price_krw": item["sale_price_krw"],
            "posted_at_utc": item.get("posted_at_utc"),
        }
        if source_fresh(row, reference=reference):
            s["pending"].append(row)
            added += 1
        else:
            s["skipped"][str(mid)] = {"reason": "STALE_OR_UNKNOWN_TIMESTAMP",
                                      "at": now_utc()}
    if items:
        s["last_scanned_id"] = max(
            s["last_scanned_id"], max(i["source_message_id"] for i in items)
        )
    s["pending"].sort(key=lambda r: r["id"])
    expired = expire(s, reference)
    validate_state(s)
    return added, expired


def _after_known_conflict(attempt: int) -> None:
    """Back off only after a confirmed 409; all other write errors stay fatal."""
    print("NAVER_GITHUB_409_REBASE_ATTEMPT=" + str(attempt + 1))
    time.sleep(min(0.35 * (attempt + 1), 1.5))


def scan(store: Store):
    # A Daangn scan or unrelated GitHub state update can advance the branch
    # while Naver is saving its *different* state file. Read and recompute
    # against the latest SHA after a verified 409; never blindly repeat a PUT.
    for attempt in range(5):
        s, sha = store.read()
        archived = False
        if s["in_flight"] is not None:
            archived = archive_stale_claim(s)
            if not archived:
                print("NAVER_PUBLISH_REVIEW_REQUIRED_NO_NEW_POST")
                return {"ready": False, "review": True}
            print("NAVER_ABANDONED_CLAIM_QUARANTINED_NO_AUTORETRY")
        discovered = old.collect_since(s["last_scanned_id"], fetch_public_html, old.older_html)
        added, expired = ingest(s, discovered)
        if archived or added or expired or discovered:
            try:
                store.write(s, sha, "state: Naver scan, quarantine stale claim and expire old deals")
            except NaverStateConflict:
                if attempt == 4:
                    raise
                _after_known_conflict(attempt)
                continue
        result = {"ready": bool(s["pending"]), "new": added, "expired": expired,
                  "archived_stale_claim": archived,
                  "pending": len(s["pending"]), "last_scanned_id": s["last_scanned_id"]}
        print("NAVER_BLOG_FIFO_SCAN " + json.dumps(result, ensure_ascii=False))
        return result
    raise RuntimeError("NAVER_SCAN_CONFLICT_RETRY_EXHAUSTED")


def make_article(source: dict, output: Path, *, tag: str,
                 manual_test_replay: bool = False):
    """Reuse the tested Daangn price/photo/link pipeline, change only blog copy.

    Manual replay is reserved for a separately authorized single-post script;
    the normal Oracle auto-publisher always uses the 60-minute freshness gate.
    """
    if manual_test_replay:
        ts = old._parse_utc(source.get("posted_at_utc"))
        if ts is None:
            raise RuntimeError("MANUAL_TEST_SOURCE_TIMESTAMP_REQUIRED")
        elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
        if not -300 <= elapsed <= 24 * 3600:
            raise RuntimeError("MANUAL_TEST_SOURCE_OLDER_THAN_24H")
    elif not source_fresh(source):
        raise RuntimeError("SOURCE_EXPIRED_OR_UNTIMED")
    output.mkdir(parents=True, exist_ok=True)
    draft = enrich_deal(source, output, owner_tag=tag)
    (output / "drafts.json").write_text(json.dumps({
        "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
        "drafts": [draft]
    }, ensure_ascii=False), encoding="utf-8")
    # The Daangn validator enforces exact item identifiers, image provenance,
    # one matching owner link, safe discount claims, and local image presence.
    verified = old.validate_draft(output, source["source_message_id"])
    # Native SmartEditor blocks give real blank paragraphs, a photo directly
    # after the disclosure, and actual bold labels (not literal Markdown).
    from gh_only.naver_blog_format import build_blocks, plain_body, validate_blocks
    url = verified["candidate_url"]
    validate_naver_link_subid(url)
    blocks = build_blocks(
        draft, verified, source, manual_test_replay=manual_test_replay
    )
    validate_blocks(blocks, url)
    body = plain_body(blocks)
    if not verified["image"]:
        raise RuntimeError("NAVER_DRAFT_UNSAFE")
    return {
        "title": naver_title(verified["title"]), "body": body, "blocks": blocks,
        "image": verified["image"],
        "source_id": source["source_message_id"], "link": url,
        "unissued_link": verified["unissued_affsdp_candidate"],
    }


def prepare_next(store: Store):
    s, _ = store.read()
    if s["in_flight"] is not None:
        raise RuntimeError("NAVER_PREVIOUS_SUBMIT_NEEDS_MANUAL_REVIEW")
    if not s["pending"]:
        print("NAVER_FIFO_EMPTY")
        return None
    source = s["pending"][0]
    if not source_fresh(source):
        raise RuntimeError("NAVER_OLDEST_POST_EXPIRED_RESCAN")
    tag = os.environ.get("COUPANG_PARTNER_LPTAG", "")
    if not tag:
        raise RuntimeError("NAVER_OWNER_TAG_MISSING")
    # Isolate every item's draft and image in a multi-post run.
    return make_article(source, DRAFT_DIR / str(source["id"]), tag=tag)


def claim_before_publish(store: Store, source_id: int) -> None:
    """Rebase only pre-submit claim writes following a known GitHub 409."""
    for attempt in range(5):
        state, sha = store.read()
        if (not state["pending"] or state["pending"][0]["id"] != source_id
                or state["in_flight"] is not None
                or str(source_id) in state["published"]
                or str(source_id) in state["skipped"]):
            raise RuntimeError("NAVER_FIFO_CHANGED_BEFORE_CLAIM")
        state["in_flight"] = {
            "id": source_id, "status": "REVIEW_REQUIRED",
            "run_id": os.environ.get("GITHUB_RUN_ID", "unknown"),
            "at": now_utc(),
        }
        try:
            store.write(state, sha, "state: Naver one-post pre-submit claim")
            return
        except NaverStateConflict:
            if attempt == 4:
                raise
            _after_known_conflict(attempt)
    raise RuntimeError("NAVER_PRE_SUBMIT_CLAIM_409_EXHAUSTED")


def checkpoint_published(store: Store, source_id: int, url: str) -> None:
    """Only retry GitHub state checkpoint after an already confirmed post.

    Never click the publish button a second time; preserve unrelated writes
    and verify the same one-source in-flight claim for each fresh SHA.
    """
    for attempt in range(5):
        state, sha = store.read()
        previously = state["published"].get(str(source_id))
        if previously is not None:
            if previously.get("url") == url and not any(
                p["id"] == source_id for p in state["pending"]
            ):
                return
            raise RuntimeError("NAVER_PUBLISHED_RECEIPT_CONFLICT")
        if (not state["in_flight"]
                or state["in_flight"]["id"] != source_id
                or not state["pending"]
                or state["pending"][0]["id"] != source_id):
            raise RuntimeError("NAVER_POST_CLAIM_LOST")
        state["pending"].pop(0)
        state["in_flight"] = None
        state["published"][str(source_id)] = {"url": url, "at": now_utc()}
        try:
            store.write(state, sha, "state: Naver blog post published exactly once")
            return
        except NaverStateConflict:
            if attempt == 4:
                raise
            _after_known_conflict(attempt)
    raise RuntimeError("NAVER_PUBLISHED_RECEIPT_409_EXHAUSTED")


def quarantine_title_failure(store: Store, source_id: int) -> None:
    """A *definite* pre-publish title failure is safe to skip, not repost."""
    for attempt in range(5):
        state, sha = store.read()
        if (not state["in_flight"]
                or state["in_flight"]["id"] != source_id
                or not state["pending"]
                or state["pending"][0]["id"] != source_id):
            raise RuntimeError("NAVER_PRE_SUBMIT_STATE_CHANGED_REVIEW_REQUIRED")
        state["pending"].pop(0)
        state["skipped"][str(source_id)] = {
            "reason": "DEFINITE_PRE_SUBMIT_TITLE_REJECTED_NO_POST",
            "claimed_run_id": os.environ.get("GITHUB_RUN_ID", "unknown"),
            "at": now_utc(),
        }
        state["in_flight"] = None
        try:
            store.write(state, sha, "state: mark title validation failure before submit")
            return
        except NaverStateConflict:
            if attempt == 4:
                raise
            _after_known_conflict(attempt)
    raise RuntimeError("NAVER_TITLE_FAILURE_409_EXHAUSTED")


def publish_one(store: Store):
    """One blog submission only; 409 retries are STATE-only, not post retries."""
    article = prepare_next(store)
    if article is None:
        return 0
    source_id = article["source_id"]
    claim_before_publish(store, source_id)
    # Any crash or uncertain confirmation leaves the claim locked. Never
    # automatically retry a second click or repeat that source ID.
    from gh_only.naver_blog_deal_publisher import (
        NaverPreSubmitTitleError, submit_one,
    )
    try:
        url = submit_one(article, os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""),
                         publish=True)
    except NaverPreSubmitTitleError:
        quarantine_title_failure(store, source_id)
        print("NAVER_PRE_SUBMIT_TITLE_ERROR_QUARANTINED_NO_POST")
        raise
    checkpoint_published(store, source_id, url)
    print("NAVER_BLOG_PUBLISHED " + json.dumps({
        "source_id": source_id, "url": url,
        "unissued_affiliate_candidate": article["unissued_link"]
    }, ensure_ascii=False))
    return 0

def expire_unclaimed_before_next(store: Store) -> int:
    """Skip stale unclaimed sources before the next queued batch item."""
    for attempt in range(5):
        state, sha = store.read()
        if state["in_flight"] is not None:
            raise RuntimeError("NAVER_PREVIOUS_SUBMIT_NEEDS_MANUAL_REVIEW")
        count = expire(state)
        if not count:
            return 0
        try:
            store.write(state, sha, "state: Naver batch discard expired unclaimed deals")
            print("NAVER_BATCH_EXPIRED_NO_POST=" + str(count))
            return count
        except NaverStateConflict:
            if attempt == 4:
                raise
            _after_known_conflict(attempt)
    raise RuntimeError("NAVER_BATCH_EXPIRE_409_EXHAUSTED")


def publish_batch(store: Store, *, max_posts: int = MAX_BATCH_POSTS) -> int:
    """Publish the oldest fresh Telegram deals sequentially and with a hard cap.

    Reuse the existing claim/submit/receipt/checkpoint path for each item.
    Never retry a possibly submitted source; any error aborts the whole batch.
    """
    if type(max_posts) is not int or not 1 <= max_posts <= MAX_BATCH_POSTS:
        raise ValueError("NAVER_BATCH_LIMIT_INVALID")
    started = time.monotonic()
    published_ids = []
    print("NAVER_BATCH_BEGIN " + json.dumps({
        "max_posts": max_posts,
        "gap_seconds": BATCH_POST_GAP_SECONDS,
        "max_elapsed_seconds": BATCH_MAX_ELAPSED_SECONDS,
    }))
    for _ in range(max_posts):
        elapsed = time.monotonic() - started
        if elapsed > BATCH_MAX_ELAPSED_SECONDS - BATCH_MIN_NEXT_POST_BUDGET_SECONDS:
            print("NAVER_BATCH_TIME_BUDGET_STOP")
            break
        if published_ids:
            # Avoid burst-posting affiliate articles without a pause.
            time.sleep(BATCH_POST_GAP_SECONDS)
        expire_unclaimed_before_next(store)
        state, _ = store.read()
        if state["in_flight"] is not None:
            raise RuntimeError("NAVER_PREVIOUS_SUBMIT_NEEDS_MANUAL_REVIEW")
        if not state["pending"]:
            break
        oldest_id = state["pending"][0]["id"]
        print("NAVER_BATCH_NEXT " + json.dumps({
            "source_id": oldest_id,
            "completed_in_run": len(published_ids),
            "remaining_queue_before_post": len(state["pending"]),
        }))
        publish_one(store)
        after, _ = store.read()
        if (str(oldest_id) not in after["published"]
                or after["in_flight"] is not None):
            raise RuntimeError("NAVER_BATCH_RECEIPT_NOT_COMMITTED_STOP")
        published_ids.append(oldest_id)
    latest, _ = store.read()
    print("NAVER_BATCH_COMPLETE " + json.dumps({
        "published_count": len(published_ids),
        "source_ids": published_ids,
        "pending_remaining": len(latest["pending"]),
    }))
    return 0


def preview_latest():
    """Manual non-publishing editor check of the newest recent Coupang deal.

    Use the latest up-to-24h source only for a manual read-only preview.
    Oracle's normal automatic source freshness window is unchanged.
    """
    from gh_only.naver_blog_latest_manual import latest_coupang, manual_source
    items = enrich_posts(fetch_public_html(), max_posts=100)
    latest = latest_coupang(items)
    if latest is None:
        print("NAVER_PREVIEW_NO_COUPANG_DEAL")
        return 0
    source = manual_source(latest)
    article = make_article(
        source, DRAFT_DIR,
        tag=os.environ.get("COUPANG_PARTNER_LPTAG", ""),
        manual_test_replay=True,
    )
    print("NAVER_PREVIEW_PREPARED " + json.dumps({
        "source_id": article["source_id"], "title_chars": len(article["title"]),
        "body_chars": len(article["body"]), "image": bool(article["image"]),
        "affiliate_candidate_unissued": article["unissued_link"],
    }))
    if os.getenv("NAVER_BLOG_STORAGE_STATE_JSON"):
        from gh_only.naver_blog_deal_publisher import submit_one
        submit_one(article, os.environ["NAVER_BLOG_STORAGE_STATE_JSON"],
                   publish=False)
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("preview", "scan", "post", "batch"))
    args = p.parse_args()
    if args.action == "preview":
        return preview_latest()
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
            or os.getenv("GITHUB_REF") != "refs/heads/main"):
        raise RuntimeError("NAVER_UNSUPPORTED_RUN_CONTEXT")
    store = Store()
    if args.action == "scan":
        result = scan(store)
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as fh:
                fh.write("ready=" + ("true" if result.get("ready") else "false") + "\n")
        return 0
    if os.getenv("NAVER_BLOG_PUBLISH_ENABLED") != "true":
        print("NAVER_BLOG_PUBLISH_PAUSED")
        return 0
    if args.action == "batch":
        return publish_batch(store)
    return publish_one(store)


if __name__ == "__main__":
    raise SystemExit(main())
