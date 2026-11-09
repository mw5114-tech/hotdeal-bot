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
        except (error.HTTPError, error.URLError, TimeoutError):
            # GitHub might have committed. Read it back; NEVER retry the PUT.
            confirmed, _ = self.read()
            if confirmed == state:
                return
            raise RuntimeError("NAVER_STATE_COMMIT_UNCERTAIN")
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


def scan(store: Store):
    s, sha = store.read()
    if s["in_flight"] is not None:
        print("NAVER_PUBLISH_REVIEW_REQUIRED_NO_NEW_POST")
        return {"ready": False, "review": True}
    discovered = old.collect_since(s["last_scanned_id"], fetch_public_html, old.older_html)
    added, expired = ingest(s, discovered)
    if added or expired or discovered:
        store.write(s, sha, "state: Naver Blog collect fresh Jirum FIFO")
    result = {"ready": bool(s["pending"]), "new": added, "expired": expired,
              "pending": len(s["pending"]), "last_scanned_id": s["last_scanned_id"]}
    print("NAVER_BLOG_FIFO_SCAN " + json.dumps(result, ensure_ascii=False))
    return result


def make_article(source: dict, output: Path, *, tag: str):
    """Reuse the tested Daangn price/photo/link pipeline, change only blog copy."""
    if not source_fresh(source):
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
    price_lines = draft["caption"]["caption"]
    if not isinstance(price_lines, str) or not price_lines.strip():
        raise RuntimeError("EMPTY_DEAL_COPY")
    image_source = draft.get("image_source")
    image_notice = (
        "\n\n※ 상품 사진은 외부 상품 검색의 대표 이미지로, 실제 옵션과 다를 수 있습니다."
        if image_source in ("fallcent_search_representative", "naver_web_first_image")
        else ""
    )
    body = (
        DISCLOSURE + "\n\n" + price_lines
        + "\n\n※ 가격과 재고는 판매처에서 실시간으로 달라질 수 있습니다."
        + image_notice
        + "\n\n출처: " + source["source_post_url"]
    )
    url = verified["candidate_url"]
    if body.count(url) != 1 or len(body) > 9000 or not verified["image"]:
        raise RuntimeError("NAVER_DRAFT_UNSAFE")
    return {
        "title": verified["title"], "body": body,
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
    return make_article(source, DRAFT_DIR, tag=tag)


def publish_one(store: Store):
    """No posting until one-time photo+copy test succeeds and flag is enabled."""
    article = prepare_next(store)
    if article is None:
        return 0
    s, sha = store.read()
    source_id = article["source_id"]
    if not s["pending"] or s["pending"][0]["id"] != source_id or s["in_flight"]:
        raise RuntimeError("NAVER_FIFO_CHANGED_BEFORE_CLAIM")
    s["in_flight"] = {
        "id": source_id, "status": "REVIEW_REQUIRED",
        "run_id": os.environ.get("GITHUB_RUN_ID", "unknown"), "at": now_utc()
    }
    store.write(s, sha, "state: Naver one-post pre-submit claim")
    # Any crash or uncertain confirmation leaves the claim locked. Never
    # automatically retry a second click or repeat that source ID.
    from gh_only.naver_blog_deal_publisher import submit_one
    url = submit_one(article, os.environ.get("NAVER_BLOG_STORAGE_STATE_JSON", ""),
                     publish=True)
    new, sha = store.read()
    if not new["in_flight"] or new["in_flight"]["id"] != source_id:
        raise RuntimeError("NAVER_POST_CLAIM_LOST")
    new["pending"].pop(0)
    new["in_flight"] = None
    new["published"][str(source_id)] = {"url": url, "at": now_utc()}
    store.write(new, sha, "state: Naver blog post published exactly once")
    print("NAVER_BLOG_PUBLISHED " + json.dumps({
        "source_id": source_id, "url": url,
        "unissued_affiliate_candidate": article["unissued_link"]
    }, ensure_ascii=False))
    return 0


def preview_latest():
    """Manual, non-publishing test using one fresh public Telegram item."""
    items = enrich_posts(fetch_public_html(), max_posts=100)
    fresh = [d for d in reversed(items)
             if d.get("url_kind") in old.COUPANG_KINDS
             and type(d.get("sale_price_krw")) is int and source_fresh(d)]
    if not fresh:
        print("NAVER_PREVIEW_NO_FRESH_ELIGIBLE_DEAL")
        return 0
    article = make_article(fresh[0], DRAFT_DIR,
                           tag=os.environ.get("COUPANG_PARTNER_LPTAG", ""))
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
    p.add_argument("action", choices=("preview", "scan", "post"))
    args = p.parse_args()
    if args.action == "preview":
        return preview_latest()
    if (os.getenv("GITHUB_ACTIONS") != "true"
            or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
            or os.getenv("GITHUB_REF") != "refs/heads/main"):
        raise RuntimeError("NAVER_UNSUPPORTED_RUN_CONTEXT")
    store = Store()
    if args.action == "scan":
        scan(store)
        return 0
    if os.getenv("NAVER_BLOG_PUBLISH_ENABLED") != "true":
        print("NAVER_BLOG_PUBLISH_PAUSED")
        return 0
    return publish_one(store)


if __name__ == "__main__":
    raise SystemExit(main())
