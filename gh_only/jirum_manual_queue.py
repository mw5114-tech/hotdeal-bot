"""Manual Jirum -> Daangn FIFO queue, persisted via main-branch GitHub Contents API.

No cron. One invocation scans all visible pages since the persisted watermark,
queues only clearly identified Coupang purchase URLs, and publishes at most one
oldest pending entry. No live write is made on an empty queue. Pre-submit claim
is persisted; uncertain attempts require a human check and are never replayed.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

from gh_only.pipeline import enrich_posts, fetch_public_html
from gh_only.daangn_editor_probe import read_one_draft

STATE_PATH = "state/daangn_jirum_manual_fifo.json"
SCHEMA = "daangn-jirum-manual-fifo-v1"
START_AFTER = 94743
MAX_PAGES = 40
MAX_PENDING = 300
MAX_STATE_BYTES = 900_000
COUPANG_KINDS = frozenset(("coupang_shortlink", "coupang_product"))
DRAFT_DIR = Path("jirum-manual-one-draft")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_state(s: dict) -> None:
    if not isinstance(s, dict) or s.get("schema") != SCHEMA:
        raise ValueError("INVALID_FIFO_SCHEMA")
    h = s.get("last_scanned_id")
    if type(h) is not int or h < START_AFTER:
        raise ValueError("INVALID_FIFO_WATERMARK")
    pending = s.get("pending")
    if not isinstance(pending, list) or len(pending) > MAX_PENDING:
        raise ValueError("INVALID_FIFO_PENDING")
    ids = [r.get("id") for r in pending if isinstance(r, dict)]
    if len(ids) != len(pending) or any(type(i) is not int or i <= START_AFTER or i > h for i in ids):
        raise ValueError("INVALID_FIFO_IDS")
    if ids != sorted(set(ids)):
        raise ValueError("UNSORTED_OR_DUPLICATE_FIFO")
    published = s.get("published")
    if not isinstance(published, dict) or any(str(i) in published for i in ids):
        raise ValueError("PUBLISHED_ITEM_STILL_QUEUED")
    if not all(k in published for k in ("94737", "94738", "94743")):
        raise ValueError("HISTORICAL_PUBLISHED_GUARD_MISSING")
    flight = s.get("in_flight")
    if flight is not None and (
        not isinstance(flight, dict)
        or type(flight.get("id")) is not int
        or flight["id"] not in ids
        or flight.get("status") not in ("CLAIMED", "REVIEW_REQUIRED")
    ):
        raise ValueError("INVALID_IN_FLIGHT")
    if type(s.get("skipped_non_coupang")) is not int:
        raise ValueError("INVALID_SKIPPED_COUNTER")


def ingest(s: dict, discovered: list[dict]) -> tuple[dict, int, int]:
    """Pure, deterministic state transition; safe to unit-test offline."""
    validate_state(s)
    if s["in_flight"] is not None:
        raise RuntimeError("IN_FLIGHT_REQUIRES_MANUAL_REVIEW")
    out = json.loads(json.dumps(s, ensure_ascii=False))
    rows = sorted(discovered, key=lambda r: r["source_message_id"])
    seen = set()
    existing = {r["id"] for r in out["pending"]}
    added = skipped = 0
    for d in rows:
        mid = d["source_message_id"]
        if type(mid) is not int or mid in seen:
            raise ValueError("DUPLICATE_OR_BAD_SOURCE_ID")
        seen.add(mid)
        if mid <= out["last_scanned_id"]:
            continue
        if d.get("url_kind") not in COUPANG_KINDS:
            skipped += 1
            continue
        if mid in existing or str(mid) in out["published"]:
            continue
        # Preserve source evidence so the oldest queued item remains usable
        # even after it scrolls off Telegram's latest public page.
        row = {
            "id": mid,
            "source_post_url": d["source_post_url"],
            "original_purchase_url": d.get("original_purchase_url"),
            "source_text": d.get("source_text", ""),
            "title": d.get("title", ""),
            "sale_price_krw": d.get("sale_price_krw"),
        }
        if not isinstance(row["original_purchase_url"], str) or not row["original_purchase_url"].startswith("https://"):
            raise ValueError("COUPANG_URL_INVALID")
        out["pending"].append(row)
        existing.add(mid)
        added += 1
    out["pending"].sort(key=lambda r: r["id"])
    if len(out["pending"]) > MAX_PENDING:
        raise RuntimeError("PENDING_BACKLOG_OVER_LIMIT_NO_PROGRESS")
    if rows:
        out["last_scanned_id"] = max(out["last_scanned_id"], rows[-1]["source_message_id"])
    out["skipped_non_coupang"] += skipped
    validate_state(out)
    if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
        raise RuntimeError("STATE_TOO_LARGE_NO_PROGRESS")
    return out, added, skipped


def older_html(before_id: int) -> str:
    url = f"https://t.me/s/jirum?before={before_id}"
    req = request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; PublicDealReader/1.0)",
        "Accept": "text/html",
    })
    with request.urlopen(req, timeout=25) as res:
        u = urlsplit(res.geturl())
        if u.scheme != "https" or u.hostname != "t.me" or u.path != "/s/jirum":
            raise RuntimeError("HISTORY_PAGE_REDIRECT")
        data = res.read(2_000_001)
    if len(data) > 2_000_000:
        raise RuntimeError("HISTORY_PAGE_TOO_LARGE")
    return data.decode("utf-8-sig", "replace")


def collect_since(watermark: int, latest_fetch=fetch_public_html, before_fetch=older_html,
                  max_pages: int = MAX_PAGES) -> list[dict]:
    """Do not silently lose older posts if more than one Telegram page arrived."""
    merged: dict[int, dict] = {}
    previous_oldest = None
    for i in range(max_pages):
        html = latest_fetch() if i == 0 else before_fetch(previous_oldest)
        batch = enrich_posts(html, max_posts=200)
        if not batch:
            raise RuntimeError("TELEGRAM_PAGE_EMPTY")
        for row in batch:
            mid = row["source_message_id"]
            if mid in merged:
                if merged[mid].get("original_purchase_url") != row.get("original_purchase_url"):
                    raise RuntimeError("TELEGRAM_OVERLAP_CONFLICT")
            else:
                merged[mid] = row
        oldest = min(row["source_message_id"] for row in batch)
        if oldest <= watermark:
            return [merged[k] for k in sorted(merged) if k > watermark]
        if previous_oldest is not None and oldest >= previous_oldest:
            raise RuntimeError("TELEGRAM_HISTORY_NOT_ADVANCING")
        previous_oldest = oldest
    raise RuntimeError("TELEGRAM_HISTORY_INCOMPLETE_NO_WATERMARK_ADVANCE")


class GithubState:
    def __init__(self):
        self.repo = os.environ.get("GITHUB_REPOSITORY", "")
        self.token = os.environ.get("GH_STATE_TOKEN", "")
        if (not self.token or not self.repo or len(self.repo.split("/")) != 2
                or self.repo != "mw5114-tech/hotdeal-bot"):
            raise RuntimeError("GITHUB_REPOSITORY_OR_STATE_TOKEN_MISSING")
        self.url = f"https://api.github.com/repos/{self.repo}/contents/{STATE_PATH}"

    def api(self, method: str, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(self.url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "JirumManualFIFO/1.0",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        with request.urlopen(req, timeout=25) as resp:
            return json.load(resp)

    def read(self) -> tuple[dict, str]:
        data = self.api("GET")
        if data.get("type") != "file" or not data.get("sha"):
            raise RuntimeError("PERSISTED_STATE_NOT_FOUND")
        content = base64.b64decode(data["content"].replace("\n", ""))
        if len(content) > MAX_STATE_BYTES:
            raise RuntimeError("PERSISTED_STATE_TOO_LARGE")
        s = json.loads(content)
        validate_state(s)
        return s, data["sha"]

    def write(self, state: dict, expected_sha: str, message: str):
        validate_state(state)
        raw = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise RuntimeError("PERSISTED_STATE_TOO_LARGE")
        # GitHub verifies previous blob SHA; conflicting writes must fail.
        result = self.api("PUT", {
            "message": message + " [skip ci]",
            "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
            "sha": expected_sha, "branch": "main",
        })
        if not result.get("commit", {}).get("sha"):
            raise RuntimeError("STATE_COMMIT_NOT_CONFIRMED")


def scan(store: GithubState, latest_fetch=fetch_public_html,
         before_fetch=older_html) -> dict:
    s, sha = store.read()
    if s["in_flight"] is not None:
        print("FIFO_REVIEW_REQUIRED source_id=" + str(s["in_flight"]["id"]))
        return {"status": "REVIEW_REQUIRED", "has_deal": False, "pending": len(s["pending"])}
    discovered = collect_since(s["last_scanned_id"], latest_fetch, before_fetch)
    new, added, skipped = ingest(s, discovered)
    if new != s:
        store.write(new, sha, "state: checkpoint new Jirum messages and Coupang FIFO")
    nxt = new["pending"][0]["id"] if new["pending"] else None
    result = {
        "status": "READY_ONE" if nxt is not None else "NO_NEW_COUPANG_DEAL",
        "has_deal": nxt is not None,
        "source_id": nxt,
        "pending": len(new["pending"]),
        "new_coupang": added, "ignored_other_sites": skipped,
        "last_scanned_id": new["last_scanned_id"],
    }
    print("JIRUM_MANUAL_SCAN " + json.dumps(result, ensure_ascii=False))
    return result


def validate_draft(folder: Path, expected_id: int) -> dict:
    report = json.loads((folder / "drafts.json").read_text("utf-8"))
    rows = report.get("drafts", [])
    if len(rows) != 1 or rows[0].get("source_message_id") != expected_id:
        raise RuntimeError("DRAFT_SOURCE_MISMATCH")
    row = rows[0]
    article = read_one_draft(folder)
    if (article["source_id"] != expected_id or article["image"] is None
        or not article["image"].is_file() or row.get("photo", {}).get("status") != "IMAGE_SAVED"
        or row.get("product_identity", {}).get("status") != "EXACT_ITEM_IDENTIFIED"
        or not row.get("kakao_product_match")
        or type(row.get("caption", {}).get("sale_price_krw")) is not int
        or "📦 상품명 :" not in article["body"]
        or "🛒 상품 링크 : " + article["candidate_url"] not in article["body"]
        or article["body"].count(article["candidate_url"]) != 1):
        raise RuntimeError("POST_EVIDENCE_NOT_READY")
    from urllib.parse import parse_qs
    link = urlsplit(article["candidate_url"])
    qs = parse_qs(link.query)
    ids = row["product_identity"]
    if (qs.get("lptag") != [os.getenv("COUPANG_PARTNER_LPTAG")]
        or qs.get("pageKey") != [ids.get("product_id")]
        or qs.get("itemId") != [ids.get("item_id")]):
        raise RuntimeError("OWNER_LINK_ITEM_OR_TAG_MISMATCH")
    return {**article, "product_id": ids["product_id"], "item_id": ids["item_id"]}


def build_next(s: dict) -> dict:
    from gh_only.default_deal_automation import enrich_deal
    validate_state(s)
    if s["in_flight"] is not None or not s["pending"]:
        raise RuntimeError("PENDING_ITEM_NOT_AVAILABLE")
    row = s["pending"][0]
    DRAFT_DIR.mkdir(exist_ok=True)
    candidate = enrich_deal({
        "source_message_id": row["id"],
        "source_post_url": row["source_post_url"],
        "original_purchase_url": row["original_purchase_url"],
        "source_text": row["source_text"],
        "title": row["title"],
        "sale_price_krw": row["sale_price_krw"],
    }, DRAFT_DIR,
        session_secret=os.getenv("KAKAO_DEV_STORAGE_STATE_JSON", ""),
        owner_tag=os.getenv("COUPANG_PARTNER_LPTAG", ""))
    (DRAFT_DIR / "drafts.json").write_text(json.dumps({
        "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
        "drafts": [candidate],
    }, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return validate_draft(DRAFT_DIR, row["id"])


def claim(store: GithubState, source_id: int, run_id: str) -> None:
    state, sha = store.read()
    if (state["in_flight"] is not None or not state["pending"]
        or state["pending"][0]["id"] != source_id
        or str(source_id) in state["published"]):
        raise RuntimeError("QUEUE_CHANGED_BEFORE_CLAIM")
    state["in_flight"] = {
        "id": source_id, "status": "CLAIMED", "run_id": run_id, "at": now_utc(),
    }
    store.write(state, sha, f"state: claim Jirum {source_id} before one post")


def finish(store: GithubState, source_id: int, run_id: str, outcome: dict) -> None:
    state, sha = store.read()
    flight = state["in_flight"]
    if not flight or flight.get("id") != source_id or flight.get("run_id") != run_id:
        raise RuntimeError("CLAIM_LOST_DO_NOT_REPOST")
    if outcome.get("status") == "POST_PUBLISHED_AND_FORMAT_VERIFIED" and outcome.get("post_url"):
        if state["pending"][0]["id"] != source_id:
            raise RuntimeError("FIFO_HEAD_CHANGED")
        state["pending"].pop(0)
        state["published"][str(source_id)] = {
            "status": "PUBLISHED_VERIFIED",
            "post_url": outcome["post_url"],
            "at": now_utc(), "run_id": run_id,
        }
        state["in_flight"] = None
    elif outcome.get("status") == "ABORTED_BEFORE_SUBMIT" and outcome.get("submit_attempted") is False:
        # Nothing was submitted; the SAME oldest item may be tried at the
        # next manual click. Do not advance the queue or take the next item.
        state["in_flight"] = None
    else:
        # A crash between claim, click, and confirmation can be ambiguous.
        # Never re-post automatically; require a human to inspect the cafe.
        state["in_flight"] = {
            **flight, "status": "REVIEW_REQUIRED",
            "last_result": outcome.get("status", "UNKNOWN"),
            "submit_attempted": bool(outcome.get("submit_attempted")),
            "checked_at": now_utc(),
        }
    store.write(state, sha, f"state: finalize Jirum {source_id} without repeat")


def check_manual_trigger() -> None:
    if (os.getenv("GITHUB_ACTIONS") != "true"
        or os.getenv("GITHUB_REF") != "refs/heads/main"
        or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
        or os.getenv("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch")):
        raise RuntimeError("NOT_MANUAL_MAIN_FIRST_ATTEMPT")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("scan", "publish"))
    args = parser.parse_args()
    check_manual_trigger()
    store = GithubState()
    if args.command == "scan":
        result = scan(store)
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as f:
                f.write("has_deal=" + ("true" if result.get("has_deal") else "false") + "\n")
        return 0
    state, _ = store.read()
    if state["in_flight"] is not None:
        raise RuntimeError("PREVIOUS_POST_REQUIRES_MANUAL_REVIEW")
    if not state["pending"]:
        print("NO_NEW_COUPANG_DEAL")
        return 0
    article = build_next(state)  # Must finish all data checks before claim
    source_id = article["source_id"]
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id:
        raise RuntimeError("GITHUB_RUN_ID_MISSING")
    claim(store, source_id, run_id)
    outcome = {"status": "PUBLISH_NOT_STARTED", "submit_attempted": False}
    try:
        from gh_only.jirum_manual_publisher import publish_one
        outcome = publish_one(article)
    except Exception as exc:
        outcome = {"status": "PUBLISH_EXCEPTION_REVIEW", "error_type": type(exc).__name__,
                   "submit_attempted": False}
    # If this write fails after the post, CLAIMED persists: fail closed.
    finish(store, source_id, run_id, outcome)
    print("JIRUM_MANUAL_PUBLISH " + json.dumps({
        "source_id": source_id, "status": outcome.get("status"),
        "post_url": outcome.get("post_url"), "submit_attempted": outcome.get("submit_attempted"),
    }, ensure_ascii=False))
    return 0 if outcome.get("status") == "POST_PUBLISHED_AND_FORMAT_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
