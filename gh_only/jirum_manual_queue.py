"""Scheduled (30min) or manual Jirum -> Daangn FIFO, persisted via GitHub Contents API.

One invocation scans all visible pages since the persisted watermark,
queues only clearly identified Coupang purchase URLs, and drains pending items
oldest first until empty or a safe time budget is reached. No live write is made
on an empty queue. Pre-submit claim
is persisted; uncertain attempts require a human check and are never replayed.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
import time
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
# Avoid GitHub 15-minute job cancellation during a live post. A subsequent
# scheduled run resumes any remaining backlog without ever replaying a post.
DRAIN_TIME_BUDGET_SECONDS = 11 * 60
MAX_DEAL_AGE_SECONDS = 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60



def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def enforcing_freshness() -> bool:
    return os.getenv("JIRUM_REQUIRE_FRESH_POST") == "true"


def lowchart_mode() -> bool:
    return os.getenv("JIRUM_IMAGE_SOURCE") == "lowchart"


def _parse_utc(raw) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 45:
        return None
    try:
        val = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return val.astimezone(timezone.utc) if val.tzinfo else None
    except ValueError:
        return None


def freshness_status(row: dict, *, reference: datetime | None = None) -> str:
    """Never publish an unknown, future-dated or stale Telegram deal."""
    posted = _parse_utc(row.get("posted_at_utc"))
    if posted is None:
        return "POSTED_AT_UNKNOWN"
    at = reference or datetime.now(timezone.utc)
    elapsed = (at - posted).total_seconds()
    if elapsed < -MAX_FUTURE_SKEW_SECONDS:
        return "POSTED_AT_IN_FUTURE"
    if elapsed > MAX_DEAL_AGE_SECONDS:
        return "DEAL_OVER_60_MINUTES_OLD"
    return "FRESH"


def expire_pending(s: dict, *, reference: datetime | None = None) -> tuple[dict, int]:
    """Archive, never erase, already queued stale deals before any browser work.

    Works on a copy so the caller can make ONE guarded GitHub state commit.
    Each historical record stays queryable and will never be reposted.
    """
    out = json.loads(json.dumps(s, ensure_ascii=False))
    keep = []
    skipped = out.setdefault("skipped_expired", {})
    count = 0
    for row in out["pending"]:
        status = freshness_status(row, reference=reference)
        if status == "FRESH":
            keep.append(row)
            continue
        mid = str(row["id"])
        if mid in out["published"] or mid in skipped:
            raise RuntimeError("STALENESS_AUDIT_CONFLICT")
        skipped[mid] = {
            "status": status,
            "source_post_url": row["source_post_url"],
            "source_title": row.get("title", "")[:180],
            "posted_at_utc": row.get("posted_at_utc"),
            "at": (reference or datetime.now(timezone.utc)).isoformat(),
        }
        count += 1
    out["pending"] = keep
    validate_state(out)
    return out, count



def kakao_secret_fingerprint() -> str:
    """Opaque marker only; never persist or display Kakao authentication data."""
    secret = os.getenv("KAKAO_DEV_STORAGE_STATE_JSON", "")
    return hashlib.sha256(("kakao-session-v1:" + secret).encode("utf-8")).hexdigest()


def kakao_block_for_current_secret(state: dict) -> dict | None:
    block = state.get("kakao_auth_block")
    if block and block["secret_fingerprint"] == kakao_secret_fingerprint():
        return block
    return None


def kakao_login_failure(folder: Path) -> str | None:
    """Read a safe diagnostic status from the prepared draft, no private data."""
    try:
        rows = json.loads((folder / "drafts.json").read_text("utf-8"))["drafts"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if len(rows) == 1 and isinstance(rows[0], dict):
        status = rows[0].get("kakao_status")
        if status in ("KAKAO_LOGIN_EXPIRED", "KAKAO_SESSION_SECRET_MISSING"):
            return status
    return None


def remember_kakao_login_block(store, source_id: int, reason: str) -> None:
    state, sha = store.read()
    if (state["in_flight"] is not None or not state["pending"]
        or state["pending"][0]["id"] != source_id):
        raise RuntimeError("QUEUE_CHANGED_BEFORE_KAKAO_BLOCK")
    state["kakao_auth_block"] = {
        "reason": reason,
        "source_id": source_id,
        "detected_at": now_utc(),
        "secret_fingerprint": kakao_secret_fingerprint(),
    }
    store.write(state, sha, "state: pause FIFO until Kakao secret renewed")


PERMANENT_LOWCHART_ISSUES = frozenset((
    "LOWCHART_TELEGRAM_TITLE_MISMATCH",
    "LOWCHART_ITEM_IDS_MISSING",
    "LOWCHART_PRODUCT_IMAGE_AMBIGUOUS",
    "LOWCHART_PRODUCT_IMAGE_NOT_FOUND",
    "LOWCHART_ITEM_PAGE_NOT_FOUND",
    "LOWCHART_TITLE_UNVERIFIED",
    "PHOTO_EVIDENCE_UNAVAILABLE",
))


def permanent_lowchart_failure(folder: Path) -> str | None:
    try:
        rows = json.loads((folder / "drafts.json").read_text("utf-8"))["drafts"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    reason = row.get("image_metadata_status")
    if reason in PERMANENT_LOWCHART_ISSUES:
        return reason
    # A source failure before any submit must not stall every newer item.
    # Keep an auditable skip rather than marking anything as published.
    if (row.get("product_identity", {}).get("status") == "EXACT_ITEM_IDENTIFIED"
            and row.get("photo", {}).get("status") != "IMAGE_SAVED"):
        return "PHOTO_EVIDENCE_UNAVAILABLE"
    if (row.get("product_identity", {}).get("status") == "EXACT_ITEM_IDENTIFIED"
            and row.get("lowchart_title_verified") is not True):
        return "LOWCHART_TITLE_UNVERIFIED"
    return None


def archive_unverifiable(store, source_id: int, reason: str) -> None:
    if reason not in PERMANENT_LOWCHART_ISSUES:
        raise ValueError("NOT_PERMANENT_IMAGE_MISMATCH")
    state, sha = store.read()
    if (state["in_flight"] is not None or not state["pending"]
            or state["pending"][0]["id"] != source_id):
        raise RuntimeError("QUEUE_CHANGED_BEFORE_IMAGE_SKIP")
    row = state["pending"].pop(0)
    state.setdefault("skipped_unverifiable", {})[str(source_id)] = {
        "status": reason, "source_post_url": row["source_post_url"],
        "source_title": row.get("title", "")[:180], "at": now_utc()
    }
    store.write(state, sha, "state: archive unverifiable Lowchart photo without posting")


def kakao_operator_notice(reason: str, pending: int) -> None:
    message = (
        "Kakao Developers login expired: update GitHub Actions secret "
        "KAKAO_DEV_STORAGE_STATE_JSON with a fresh logged-in storage_state. "
        "Existing pending posts are preserved; publication is paused."
    )
    print("::warning title=Kakao login renewal required::" + message)
    print("JIRUM_KAKAO_AUTH_PAUSED " + json.dumps({
        "reason": reason, "pending": pending, "publishing_enabled": False,
        "next_step": "RENEW_KAKAO_DEV_STORAGE_STATE_JSON",
    }, ensure_ascii=False))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write(
                "### ⏸️ Daangn posting paused: Kakao login expired\n\n"
                f"- Pending deals: **{pending}** (kept; none discarded)\n"
                "- No post was submitted during this failed preparation.\n"
                "- Update repository Actions secret `KAKAO_DEV_STORAGE_STATE_JSON` "
                "with a fresh Kakao Developers login session.\n"
                "- The next scan automatically resumes when the secret changes.\n\n"
            )




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
    skipped_expired = s.get("skipped_expired", {})
    if (not isinstance(skipped_expired, dict)
            or len(skipped_expired) > 10_000
            or any(not isinstance(k, str) or not k.isdigit()
                   or not isinstance(v, dict) or
                   v.get("status") not in (
                       "POSTED_AT_UNKNOWN", "POSTED_AT_IN_FUTURE",
                       "DEAL_OVER_60_MINUTES_OLD")
                   for k, v in skipped_expired.items())
            or any(str(i) in skipped_expired for i in ids)
            or any(k in published for k in skipped_expired)):
        raise ValueError("INVALID_EXPIRED_AUDIT")
    skipped_unverifiable = s.get("skipped_unverifiable", {})
    if (not isinstance(skipped_unverifiable, dict)
            or len(skipped_unverifiable) > 10_000
            or any(not isinstance(k, str) or not k.isdigit()
                   or not isinstance(v, dict) or
                   v.get("status") not in (
                     "LOWCHART_TELEGRAM_TITLE_MISMATCH",
                     "LOWCHART_ITEM_IDS_MISSING",
                     "LOWCHART_PRODUCT_IMAGE_AMBIGUOUS",
                     "LOWCHART_PRODUCT_IMAGE_NOT_FOUND",
                     "LOWCHART_ITEM_PAGE_NOT_FOUND",
                     "LOWCHART_TITLE_UNVERIFIED",
                     "PHOTO_EVIDENCE_UNAVAILABLE")
                   for k, v in skipped_unverifiable.items())
            or any(str(i) in skipped_unverifiable for i in ids)
            or any(k in published or k in skipped_expired for k in skipped_unverifiable)):
        raise ValueError("INVALID_UNVERIFIABLE_AUDIT")
    block = s.get("kakao_auth_block")
    if block is not None and (
        not isinstance(block, dict)
        or block.get("reason") not in ("KAKAO_LOGIN_EXPIRED", "KAKAO_SESSION_SECRET_MISSING")
        or type(block.get("source_id")) is not int
        or not isinstance(block.get("detected_at"), str)
        or not isinstance(block.get("secret_fingerprint"), str)
        or len(block["secret_fingerprint"]) != 64
        or any(ch not in "0123456789abcdef" for ch in block["secret_fingerprint"])
    ):
        raise ValueError("INVALID_KAKAO_AUTH_BLOCK")


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
            "posted_at_utc": d.get("posted_at_utc"),
            "title": d.get("title", ""),
            "sale_price_krw": d.get("sale_price_krw"),
        }
        if not isinstance(row["original_purchase_url"], str) or not row["original_purchase_url"].startswith("https://"):
            raise ValueError("COUPANG_URL_INVALID")
        if (d.get("image_provenance") == "telegram_attached_photo"
                and isinstance(d.get("image_url_candidate"), str)):
            row["image_provenance"] = "telegram_attached_photo"
            row["image_url_candidate"] = d["image_url_candidate"]
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
    if lowchart_mode():
        # The new public photo path never needs the old Kakao auth blocker.
        new.pop("kakao_auth_block", None)
    expired = 0
    if enforcing_freshness():
        # Legacy queue entries lacked date metadata. Backfill only from
        # Telegram's current public, timestamped window. Unknown = not safe
        # for hotdeal publication, but each skipped entry stays audited.
        legacy = {x["id"] for x in new["pending"]
                  if _parse_utc(x.get("posted_at_utc")) is None}
        missing_photo = lowchart_mode() and any(
            not item.get("image_url_candidate") for item in new["pending"])
        if legacy or missing_photo:
            try:
                recent = enrich_posts(latest_fetch(), max_posts=200)
                by_id = {x["source_message_id"]: x for x in recent}
                for item in new["pending"]:
                    source = by_id.get(item["id"], {})
                    if item["id"] in legacy and _parse_utc(source.get("posted_at_utc")):
                        item["posted_at_utc"] = source["posted_at_utc"]
                    if (not item.get("image_url_candidate")
                            and source.get("image_provenance") == "telegram_attached_photo"
                            and isinstance(source.get("image_url_candidate"), str)):
                        item["image_provenance"] = "telegram_attached_photo"
                        item["image_url_candidate"] = source["image_url_candidate"]
            except Exception as exc:
                print("JIRUM_SOURCE_MEDIA_BACKFILL_UNAVAILABLE " + type(exc).__name__)
        new, expired = expire_pending(new)
    if new != s:
        store.write(new, sha, "state: checkpoint new Jirum messages, freshness and FIFO")
    block = new.get("kakao_auth_block")
    # A newly saved GitHub Secret is automatically eligible for one fresh
    # attempt. The old invalid authentication material can never trigger the
    # expensive Playwright step again until the fingerprint changes.
    if block and not kakao_block_for_current_secret(new):
        new.pop("kakao_auth_block")
        if new != s:
            # Ingest may have already persisted the new Telegram cursor; re-read
            # to preserve the new blob SHA and avoid overriding concurrent work.
            latest, latest_sha = store.read()
            latest.pop("kakao_auth_block", None)
            store.write(latest, latest_sha, "state: fresh Kakao secret unblocks FIFO")
        block = None
        print("JIRUM_KAKAO_AUTH_REFRESHED_TRY_AGAIN")
    nxt = new["pending"][0]["id"] if new["pending"] else None
    paused = bool(block and kakao_block_for_current_secret(new) and nxt is not None)
    result = {
        "status": ("KAKAO_REAUTH_REQUIRED" if paused
                   else "READY_ONE" if nxt is not None else "NO_NEW_COUPANG_DEAL"),
        "has_deal": nxt is not None and not paused,
        "source_id": nxt,
        "pending": len(new["pending"]),
        "new_coupang": added, "ignored_other_sites": skipped,
        "expired_without_post": expired,
        "last_scanned_id": new["last_scanned_id"],
    }
    print("JIRUM_MANUAL_SCAN " + json.dumps(result, ensure_ascii=False))
    if paused:
        kakao_operator_notice(block["reason"], len(new["pending"]))
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
        or not ((
            (row.get("image_product_match") is True
             and row.get("image_source") == "lowchart_exact_item_public_page"
             and row.get("image_exact_item_page_verified") is True)
            or (row.get("image_source") == "telegram_attached_photo"
                and row.get("source_photo_verified") is True
                and row.get("photo", {}).get("image_source") == "telegram_attached_photo")
        ) if lowchart_mode() else row.get("kakao_product_match"))
        or type(row.get("caption", {}).get("sale_price_krw")) is not int
        or (lowchart_mode() and (
            row.get("lowchart_title_verified") is not True
            or row.get("product_name_source") != "lowchart_exact_item_page"
            or not isinstance(row.get("lowchart_product_title"), str)
            or not isinstance(row.get("caption", {}).get("product_name"), str)
            or article["body"].splitlines()[0] != row["caption"]["caption"].splitlines()[0]
            or not article["body"].splitlines()[0].endswith(row["caption"]["product_name"])
            or "[쿠팡🔥핫딜] " + row["caption"]["product_name"] not in article["title"]
        ))
        or "📦 상품명 :" in article["body"]
        or "🛒 쿠팡에서 상품정보 더보기 : " + article["candidate_url"] not in article["body"]
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
        "image_provenance": row.get("image_provenance"),
        "image_url_candidate": row.get("image_url_candidate"),
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


def check_run_trigger() -> None:
    if (os.getenv("GITHUB_ACTIONS") != "true"
        or os.getenv("GITHUB_REF") != "refs/heads/main"
        or os.getenv("GITHUB_RUN_ATTEMPT") != "1"
        or os.getenv("GITHUB_EVENT_NAME") not in ("push", "workflow_dispatch", "schedule")):
        raise RuntimeError("NOT_MAIN_FIRST_ATTEMPT_OR_WRONG_EVENT")


def publish_pending(store: GithubState, *, drain: bool = False,
                    clock=None) -> int:
    """Publish FIFO entries sequentially; checkpoint after EVERY verified post.

    Fail closed on any failed or uncertain publish. There is no retry within
    the same run. The next scheduled scan picks up any unfinished backlog.
    """
    clock = clock or time.monotonic
    started = clock()
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id:
        raise RuntimeError("GITHUB_RUN_ID_MISSING")
    completed = 0
    while True:
        state, _ = store.read()
        if state["in_flight"] is not None:
            raise RuntimeError("PREVIOUS_POST_REQUIRES_MANUAL_REVIEW")
        if enforcing_freshness() and state["pending"]:
            updated, n_expired = expire_pending(state)
            if n_expired:
                _, sha = store.read()
                store.write(updated, sha, "state: archive timed-out deals before browser/publish")
                print("JIRUM_STALE_BACKLOG_ARCHIVED " + json.dumps({
                    "count": n_expired, "remaining": len(updated["pending"]),
                }))
                state = updated
        if not state["pending"]:
            print("JIRUM_FIFO_DRAIN_COMPLETE " + json.dumps({
                "published_this_run": completed,
                "pending": 0,
                "reason": "QUEUE_EMPTY",
            }))
            return 0
        # Check budget only BETWEEN posts; never cut off mid-submission.
        if drain and completed and clock() - started >= DRAIN_TIME_BUDGET_SECONDS:
            print("JIRUM_FIFO_DRAIN_PAUSED " + json.dumps({
                "published_this_run": completed,
                "pending": len(state["pending"]),
                "reason": "SAFE_RUNTIME_BUDGET_NEXT_SCHEDULE_RESUMES",
            }))
            return 0

        try:
            article = build_next(state)  # Full evidence validation before claim.
        except RuntimeError as exc:
            # A login-expired Kakao session cannot supply a verified product
            # image. Publishing must not claim/post anything; checkpoint once,
            # then skip future Playwright work until the Secret changes.
            if str(exc) == "POST_EVIDENCE_NOT_READY" and lowchart_mode():
                permanent = permanent_lowchart_failure(DRAFT_DIR)
                if permanent:
                    source_id = state["pending"][0]["id"]
                    archive_unverifiable(store, source_id, permanent)
                    print("JIRUM_LOWCHART_UNVERIFIABLE_ARCHIVED " + json.dumps({
                        "source_id": source_id, "reason": permanent,
                        "no_post_submitted": True,
                    }))
                    continue
            reason = (kakao_login_failure(DRAFT_DIR)
                      if str(exc) == "POST_EVIDENCE_NOT_READY" and not lowchart_mode()
                      else None)
            if reason:
                source_id = state["pending"][0]["id"]
                remember_kakao_login_block(store, source_id, reason)
                kakao_operator_notice(reason, len(state["pending"]))
                return 2
            raise
        source_id = article["source_id"]
        claim(store, source_id, run_id)  # Durable pre-click checkpoint.
        outcome = {"status": "PUBLISH_NOT_STARTED", "submit_attempted": False}
        try:
            from gh_only.jirum_manual_publisher import publish_one
            outcome = publish_one(article)
        except Exception as exc:
            # Unexpected crash might have happened at the browser boundary;
            # preserve review status rather than risk a duplicate post.
            outcome = {
                "status": "PUBLISH_EXCEPTION_REVIEW",
                "error_type": type(exc).__name__,
                "submit_attempted": False,
            }
        # If the write fails after clicking, CLAIMED remains, blocking replay.
        finish(store, source_id, run_id, outcome)
        print("JIRUM_MANUAL_PUBLISH " + json.dumps({
            "source_id": source_id, "status": outcome.get("status"),
            "post_url": outcome.get("post_url"),
            "submit_attempted": outcome.get("submit_attempted"),
            "failure_stage": outcome.get("failure_stage"),
            "error_type": outcome.get("error_type"),
            "error_reason": outcome.get("error_reason"),
            "http_status": outcome.get("http_status"),
            "page_path": outcome.get("page_path"),
            "editor_checks": outcome.get("editor_checks"),
            "title_checks": outcome.get("title_checks"),
            "submit_checks": outcome.get("submit_checks"),
            "photo_upload_completed": outcome.get("photo_upload_completed"),
        }, ensure_ascii=False))

        if outcome.get("status") != "POST_PUBLISHED_AND_FORMAT_VERIFIED":
            # STOP on first failure. For pre-submit failure the head remains,
            # and for uncertain submissions the queue is review-locked.
            print("JIRUM_FIFO_DRAIN_STOPPED " + json.dumps({
                "published_this_run": completed,
                "failed_source_id": source_id,
                "status": outcome.get("status"),
            }))
            return 2
        completed += 1
        print("JIRUM_FIFO_PROGRESS " + json.dumps({
            "published_this_run": completed,
            "last_source_id": source_id,
        }))
        if not drain:
            return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("scan", "publish", "drain"))
    args = parser.parse_args()
    check_run_trigger()
    store = GithubState()
    if args.command == "scan":
        result = scan(store)
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as f:
                f.write("has_deal=" + ("true" if result.get("has_deal") else "false") + "\n")
        return 0
    return publish_pending(store, drain=args.command == "drain")


if __name__ == "__main__":
    raise SystemExit(main())
