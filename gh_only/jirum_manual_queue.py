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

from gh_only.pipeline import enrich_posts, fetch_public_html, read_public_telegram_html
from gh_only.daangn_editor_probe import read_one_draft
from gh_only.official_link_receipts import load_receipt

STATE_PATH = "state/daangn_jirum_manual_fifo.json"
BUSAN_STATE_PATH = "state/daangn_busan_over30_fifo.json"


def busan_cafe_mode() -> bool:
    return os.getenv("DAANGN_BUSAN_CAFE_MODE") == "true"


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
# Telegram's public page can lag the MTProto event or omit the timestamp on
# an individual page. Never guess publication time from the GitHub run time.
TIMESTAMP_RETRY_GRACE_SECONDS = 3 * 60
MAX_DIRECT_TIMESTAMP_LOOKUPS = 3
# A workflow job has a 15-minute hard timeout. Old claims must be archived,
# never automatically reposted, after a generous 25-minute grace period.
STALE_CLAIM_SECONDS = 25 * 60
TRANSIENT_GITHUB_HTTP_CODES = frozenset((429, 500, 502, 503, 504))
GITHUB_READ_ATTEMPTS = 4
# GitHub can reject a Daangn state PUT with 409 if Naver committed a
# different file to the same branch at the same instant. Retry the PUT only
# if a fresh GET proves that THIS exact Daangn state blob is unchanged.
GITHUB_SAFE_409_ATTEMPTS = 7
# GitHub's Contents API can briefly return the previous blob immediately
# after a successful PUT. Poll state only; never resubmit an external post.
PUBLISHED_CHECKPOINT_VISIBILITY_ATTEMPTS = 12



def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def enforcing_freshness() -> bool:
    return os.getenv("JIRUM_REQUIRE_FRESH_POST") == "true"


def official_link_mode() -> bool:
    return os.getenv("JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK") == "true"


def muse_postedit_mode() -> bool:
    # Publish a plain product URL first; Muse replaces it in Daangn afterward.
    return os.getenv("JIRUM_PUBLISH_BEFORE_MUSE_REWRITE") == "true"


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
    # Explicit one-time user replay of the previously skipped TV and egg deals.
    # The original posted timestamp is retained; do not exempt future deals.
    if (row.get("manual_replay_approved") is True and
            row.get("id") in (94986, 94987, 94988, 94989, 94990)):
        return "FRESH"
    if elapsed >= MAX_DEAL_AGE_SECONDS:
        return "DEAL_OVER_60_MINUTES_OLD"
    return "FRESH"


def awaiting_source_timestamp(row: dict, *, reference: datetime | None = None) -> bool:
    """A newly observed *untimed* post may be rechecked briefly, never posted."""
    if _parse_utc(row.get("posted_at_utc")) is not None:
        return False
    seen = _parse_utc(row.get("first_seen_at_utc"))
    if seen is None:
        return False  # Pre-existing untimed legacy posts remain fail-closed.
    age = ((reference or datetime.now(timezone.utc)) - seen).total_seconds()
    return -MAX_FUTURE_SKEW_SECONDS <= age < TIMESTAMP_RETRY_GRACE_SECONDS


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
        if status == "FRESH" or (status == "POSTED_AT_UNKNOWN"
                                 and awaiting_source_timestamp(row, reference=reference)):
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
    from gh_only.naver_api_image_photo import eligible_draft as naver_api_eligible
    if naver_api_eligible(row):
        return None
    # A documented search representative (Fallcent or Naver) may replace
    # a missing Lowchart image; other unverified photo types may not.
    if (row.get("photo", {}).get("status") == "IMAGE_SAVED"
            and (
                (row.get("image_source") == "fallcent_search_representative"
                 and row.get("representative_fallcent_image") is True
                 and row.get("image_match_basis") == "UNVERIFIED_FALLCENT_SEARCH_RESULT")
                or (row.get("image_source") == "naver_web_first_image"
                    and row.get("representative_naver_image") is True
                    and row.get("image_match_basis") == "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT")
            )
            and row.get("photo", {}).get("image_source") == row.get("image_source")):
        return None
    if row.get("photo", {}).get("status") == "PHOTO_EVIDENCE_UNAVAILABLE":
        print("JIRUM_PHOTO_SOURCES_EXHAUSTED " + json.dumps({
            "source_id": row.get("source_message_id"),
            "lowchart": row.get("image_metadata_status"),
            "fallback_attempts": row.get("photo_provider_attempts", []),
        }, ensure_ascii=False))
        return "PHOTO_EVIDENCE_UNAVAILABLE"
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


def defer_transient_lowchart_photo(store, source_id: int, folder: Path) -> bool:
    """Give a pre-submit Lowchart transport outage one later runner attempt.

    Only retry a transport error; wrong-item identity and ambiguous images
    remain permanent. Keep the original FIFO row and source timestamp so the
    normal 60-minute freshness gate still prevents late publication.
    """
    if os.getenv("DAANGN_LOWCHART_TRANSIENT_RETRY_ONCE") != "true":
        return False
    try:
        drafts = json.loads((folder / "drafts.json").read_text("utf-8"))["drafts"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return False
    if (len(drafts) != 1 or not isinstance(drafts[0], dict)
            or drafts[0].get("image_metadata_status") != "LOWCHART_IMAGE_PAGE_REQUEST_ERROR"
            or drafts[0].get("photo", {}).get("status") == "IMAGE_SAVED"):
        return False
    state, sha = store.read()
    if (state["in_flight"] is not None or not state["pending"]
            or state["pending"][0]["id"] != source_id):
        raise RuntimeError("DAANGN_PHOTO_RETRY_QUEUE_CHANGED")
    row = state["pending"][0]
    count = row.get("photo_prepare_failure_count", 0)
    if type(count) is not int or not 0 <= count <= 1:
        raise RuntimeError("DAANGN_PHOTO_RETRY_BAD_COUNTER")
    if count == 1:
        return False
    row["photo_prepare_failure_count"] = 1
    store.write(state, sha, "state: defer Daangn transient Lowchart photo one run")
    print("JIRUM_LOWCHART_PHOTO_DEFERRED " + json.dumps({
        "source_id": source_id, "attempts": 1, "no_post_submitted": True,
    }))
    return True


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
                     "PHOTO_EVIDENCE_UNAVAILABLE",
                     "PUBLISH_OUTCOME_UNKNOWN_NO_RETRY")
                   for k, v in skipped_unverifiable.items())
            or any(str(i) in skipped_unverifiable for i in ids)
            or any(k in published or k in skipped_expired for k in skipped_unverifiable)):
        raise ValueError("INVALID_UNVERIFIABLE_AUDIT")
    busan_skips = s.get("skipped_busan_discount", {})
    if (not isinstance(busan_skips, dict) or len(busan_skips) > 10000
            or any(not isinstance(k, str) or not k.isdigit()
                   or not isinstance(v, dict)
                   or v.get("status") != "NOT_VERIFIED_OVER_30"
                   for k, v in busan_skips.items())
            or any(str(i) in busan_skips for i in ids)
            or any(k in published or k in skipped_expired or k in skipped_unverifiable
                   for k in busan_skips)):
        raise ValueError("INVALID_BUSAN_DISCOUNT_AUDIT")
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


def ingest(s: dict, discovered: list[dict], *,
           first_seen_at_utc: str | None = None) -> tuple[dict, int, int]:
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
        if _parse_utc(row["posted_at_utc"]) is None and first_seen_at_utc is not None:
            if _parse_utc(first_seen_at_utc) is None:
                raise ValueError("INVALID_FIRST_SEEN_TIMESTAMP")
            # This is *observation* time, not proof of Telegram post time.
            row["first_seen_at_utc"] = first_seen_at_utc
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
    # History pagination must tolerate the same transient Telegram 502s as
    # the newest page, without ever skipping an unseen FIFO source message.
    if type(before_id) is not int or before_id <= 0:
        raise ValueError("INVALID_TELEGRAM_BEFORE_ID")
    return read_public_telegram_html(
        f"https://t.me/s/jirum?before={before_id}",
        redirect_error="HISTORY_PAGE_REDIRECT",
        too_large_error="HISTORY_PAGE_TOO_LARGE",
    )

def fetch_exact_source_post(source_id: int) -> dict | None:
    """Try the exact public Telegram history boundary for a missing timestamp.

    A history page can contain multiple posts; only an exact matching ID is
    acceptable. HTTPS/host/response-size restrictions are in older_html().
    """
    if type(source_id) is not int or source_id <= START_AFTER or source_id >= 2**31 - 1:
        raise ValueError("INVALID_TELEGRAM_SOURCE_ID")
    batch = enrich_posts(older_html(source_id + 1), max_posts=200)
    return next((post for post in batch if post["source_message_id"] == source_id), None)


def matching_source_timestamp(row: dict, source: dict | None) -> str | None:
    """No timestamp trust without exact ID, same source URL, and timezone."""
    if (not isinstance(source, dict)
            or source.get("source_message_id") != row["id"]
            or source.get("original_purchase_url") != row["original_purchase_url"]):
        return None
    observed = source.get("posted_at_utc")
    return _parse_utc(observed).isoformat() if _parse_utc(observed) else None


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
        path = BUSAN_STATE_PATH if busan_cafe_mode() else STATE_PATH
        self.url = f"https://api.github.com/repos/{self.repo}/contents/{path}"

    def api(self, method: str, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(self.url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "JirumManualFIFO/1.0",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        # GET is safe to retry for GitHub 5xx/429 and transient network errors.
        # Never automatically retry a PUT: it could have committed already.
        attempts = GITHUB_READ_ATTEMPTS if method == "GET" else 1
        for index in range(attempts):
            try:
                with request.urlopen(req, timeout=25) as resp:
                    return json.load(resp)
            except (error.HTTPError, error.URLError, TimeoutError) as exc:
                transient = (exc.code in TRANSIENT_GITHUB_HTTP_CODES
                             if isinstance(exc, error.HTTPError) else True)
                if not transient or index + 1 == attempts:
                    raise
                time.sleep(min(2 ** index, 4))

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
        payload = {
            "message": message + " [skip ci]",
            "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
            "sha": expected_sha, "branch": "main",
        }
        for attempt in range(GITHUB_SAFE_409_ATTEMPTS):
            try:
                result = self.api("PUT", payload)
            except error.HTTPError as exc:
                if exc.code == 409:
                    # HTTP 409 is an explicit rejected write, not a timeout.
                    # A concurrent Naver commit can move main without changing
                    # THIS file's blob SHA. Never overwrite a changed FIFO.
                    latest, latest_sha = self.read()
                    if latest == state:
                        print("JIRUM_GITHUB_409_ALREADY_COMMITTED")
                        return
                    if latest_sha != expected_sha:
                        raise RuntimeError(
                            "DAANGN_STATE_FILE_CHANGED_NO_BLIND_REBASE"
                        ) from exc
                    if attempt + 1 == GITHUB_SAFE_409_ATTEMPTS:
                        raise RuntimeError(
                            "DAANGN_GITHUB_409_SAFE_RETRIES_EXHAUSTED"
                        ) from exc
                    print("JIRUM_GITHUB_409_RETRY_UNCHANGED_STATE attempt=" +
                          str(attempt + 1))
                    time.sleep(min(0.35 * (attempt + 1), 1.4))
                    continue
                if exc.code not in TRANSIENT_GITHUB_HTTP_CODES:
                    raise
                # A 5xx/429 or transport exception may occur AFTER commit;
                # never retry a write unless a later human review approves.
                latest, _ = self.read()
                if latest == state:
                    print("JIRUM_GITHUB_WRITE_CONFIRMED_AFTER_RESPONSE_ERROR")
                    return
                raise RuntimeError(
                    "STATE_COMMIT_UNCERTAIN_NO_BLIND_RETRY"
                ) from exc
            except (error.URLError, TimeoutError) as exc:
                latest, _ = self.read()
                if latest == state:
                    print("JIRUM_GITHUB_WRITE_CONFIRMED_AFTER_RESPONSE_ERROR")
                    return
                raise RuntimeError(
                    "STATE_COMMIT_UNCERTAIN_NO_BLIND_RETRY"
                ) from exc
            if not result.get("commit", {}).get("sha"):
                raise RuntimeError("STATE_COMMIT_NOT_CONFIRMED")
            return
        raise RuntimeError("DAANGN_GITHUB_409_SAFE_RETRIES_EXHAUSTED")


def archive_abandoned_claim(store, *, reference: datetime | None = None) -> bool:
    """Quarantine a dead publisher's claim, without replaying that source ID.

    A completed/expired Actions job might already have published to Daangn.
    Preserve an explicit uncertain audit record rather than claiming success.
    """
    state, sha = store.read()
    flight = state["in_flight"]
    if flight is None:
        return False
    reference = reference or datetime.now(timezone.utc)
    claimed = _parse_utc(flight.get("at"))
    if claimed is None or (reference - claimed).total_seconds() < STALE_CLAIM_SECONDS:
        return False
    if not state["pending"] or state["pending"][0]["id"] != flight["id"]:
        raise RuntimeError("ABANDONED_CLAIM_FIFO_INCONSISTENT")
    row = state["pending"].pop(0)
    state.setdefault("skipped_unverifiable", {})[str(row["id"])] = {
        "status": "PUBLISH_OUTCOME_UNKNOWN_NO_RETRY",
        "source_post_url": row["source_post_url"],
        "source_title": row.get("title", "")[:180],
        "claimed_run_id": flight.get("run_id"),
        "claimed_at": flight.get("at"),
        "previous_claim_status": flight.get("status"),
        "may_already_be_published": True,
        "at": reference.isoformat(),
    }
    state["in_flight"] = None
    store.write(state, sha, "state: quarantine abandoned claim without repost")
    print("JIRUM_ABANDONED_CLAIM_QUARANTINED " + json.dumps({
        "source_id": row["id"], "claimed_run_id": flight.get("run_id"),
        "reposted": False, "needs_post_audit": True,
    }))
    return True


def scan(store: GithubState, latest_fetch=fetch_public_html,
         before_fetch=older_html, *, timestamp_fetcher=None) -> dict:
    s, sha = store.read()
    if s["in_flight"] is not None:
        if archive_abandoned_claim(store):
            s, sha = store.read()
        else:
            print("FIFO_REVIEW_REQUIRED source_id=" + str(s["in_flight"]["id"]))
            return {"status": "REVIEW_REQUIRED", "has_deal": False, "pending": len(s["pending"])}
    discovered = collect_since(s["last_scanned_id"], latest_fetch, before_fetch)
    new, added, skipped = ingest(
        s, discovered, first_seen_at_utc=now_utc() if timestamp_fetcher else None
    )
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
                    confirmed = matching_source_timestamp(item, source)
                    if item["id"] in legacy and confirmed:
                        item["posted_at_utc"] = confirmed
                    if (not item.get("image_url_candidate")
                            and source.get("image_provenance") == "telegram_attached_photo"
                            and isinstance(source.get("image_url_candidate"), str)):
                        item["image_provenance"] = "telegram_attached_photo"
                        item["image_url_candidate"] = source["image_url_candidate"]
            except Exception as exc:
                print("JIRUM_SOURCE_MEDIA_BACKFILL_UNAVAILABLE " + type(exc).__name__)
        if timestamp_fetcher is not None:
            missing = [row for row in new["pending"]
                       if _parse_utc(row.get("posted_at_utc")) is None]
            # Bound network work: never repeatedly fetch hundreds of old IDs.
            for item in missing[:MAX_DIRECT_TIMESTAMP_LOOKUPS]:
                try:
                    source = timestamp_fetcher(item["id"])
                    verified = matching_source_timestamp(item, source)
                    if verified is not None:
                        item["posted_at_utc"] = verified
                        print("JIRUM_SOURCE_TIMESTAMP_CONFIRMED id=" + str(item["id"]))
                    else:
                        print("JIRUM_SOURCE_TIMESTAMP_NOT_CONFIRMED id=" + str(item["id"]))
                except Exception as exc:
                    print("JIRUM_SOURCE_TIMESTAMP_PROBE_UNAVAILABLE " + type(exc).__name__)
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
    timestamp_wait = bool(new["pending"] and awaiting_source_timestamp(new["pending"][0]))
    official_ready = True
    if official_link_mode() and nxt is not None:
        try:
            official_ready = load_receipt(new["pending"][0]) is not None
        except ValueError as exc:
            print("JIRUM_OFFICIAL_LINK_RECEIPT_REJECTED " + str(exc))
            official_ready = False
    result = {
        "status": ("KAKAO_REAUTH_REQUIRED" if paused
                   else "WAIT_SOURCE_TIMESTAMP" if timestamp_wait
                   else "READY_ONE" if nxt is not None else "NO_NEW_COUPANG_DEAL"),
        "has_deal": nxt is not None and not paused and not timestamp_wait and official_ready,
        "official_link_ready": official_ready if nxt is not None else False,
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


def verify_lowchart_mean_in_article(caption: dict, article_body: str) -> None:
    """Never publish an accepted exact-item mean without its actual price."""
    if caption.get("comparison_reference_source") != "lowchart_public_history":
        return
    price = caption.get("comparison_reference_price_krw")
    if (type(price) is not int or price <= 0
            or f"💸 쿠팡 평소 판매가 : 약 {price:,}원" not in article_body):
        raise RuntimeError("LOWCHART_AVERAGE_DROPPED_FROM_POST_NO_SUBMIT")


def validate_draft(folder: Path, expected_id: int) -> dict:
    report = json.loads((folder / "drafts.json").read_text("utf-8"))
    rows = report.get("drafts", [])
    if len(rows) != 1 or rows[0].get("source_message_id") != expected_id:
        raise RuntimeError("DRAFT_SOURCE_MISMATCH")
    row = rows[0]
    article = read_one_draft(folder)
    from gh_only.naver_api_image_photo import eligible_draft as naver_api_eligible
    api_image_ok = naver_api_eligible(row)
    if (article["source_id"] != expected_id or article["image"] is None
        or not article["image"].is_file() or row.get("photo", {}).get("status") != "IMAGE_SAVED"
        or row.get("product_identity", {}).get("status") != "EXACT_ITEM_IDENTIFIED"
        or not ((
            (row.get("image_product_match") is True
             and row.get("image_source") == "lowchart_exact_item_public_page"
             and row.get("image_exact_item_page_verified") is True)
            or api_image_ok
            or (row.get("image_source") == "telegram_attached_photo"
                and row.get("source_photo_verified") is True
                and row.get("photo", {}).get("image_source") == "telegram_attached_photo")
            or (row.get("image_source") == "fallcent_search_representative"
                and row.get("representative_fallcent_image") is True
                and row.get("image_match_basis") == "UNVERIFIED_FALLCENT_SEARCH_RESULT"
                and row.get("photo", {}).get("image_source") == "fallcent_search_representative")
            or (row.get("image_source") == "naver_web_first_image"
                and row.get("representative_naver_image") is True
                and row.get("image_match_basis") == "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT"
                and row.get("photo", {}).get("image_source") == "naver_web_first_image")
            or (row.get("image_source") in ("couspi_product_page",
                                           "dangapick_product_page")
                and row.get("image_provider_verified") is True
                and row.get("image_provider_product_id") == row.get(
                    "product_identity", {}).get("product_id")
                and row.get("photo", {}).get("image_source") ==
                    row.get("image_source"))
        ) if lowchart_mode() else row.get("kakao_product_match"))
        or type(row.get("caption", {}).get("sale_price_krw")) is not int
        or (lowchart_mode() and (
            not (
                (row.get("lowchart_title_verified") is True
                 and row.get("product_name_source") == "lowchart_exact_item_page"
                 and isinstance(row.get("lowchart_product_title"), str))
                or api_image_ok
                or (row.get("image_source") == "fallcent_search_representative"
                    and row.get("representative_fallcent_image") is True
                    and row.get("product_name_source") == "telegram_product_title"
                    and isinstance(row.get("caption", {}).get("product_name"), str))
                or (row.get("image_source") == "naver_web_first_image"
                    and row.get("representative_naver_image") is True
                    and row.get("product_name_source") == "telegram_product_title"
                    and isinstance(row.get("caption", {}).get("product_name"), str))
                or (row.get("image_source") in ("couspi_product_page",
                                              "dangapick_product_page")
                    and row.get("image_provider_verified") is True
                    and row.get("product_name_source") == "external_product_id_page"
                    and isinstance(row.get("image_provider_title"), str)
                    and row.get("caption", {}).get("product_name") ==
                        row.get("image_provider_title")[:len(
                            row.get("caption", {}).get("product_name", ""))])
            )
            or not isinstance(row.get("caption", {}).get("product_name"), str)
            or article["body"].splitlines()[0] != row["caption"]["caption"].splitlines()[0]
            or not article["body"].splitlines()[0].endswith(row["caption"]["product_name"])
            or "[쿠팡🔥핫딜] " + row["caption"]["product_name"] not in article["title"]
        ))
        or "📦 상품명 :" in article["body"]
        or "🛒 쿠팡에서 상품정보 더보기 : " + article["candidate_url"] not in article["body"]
        or article["body"].count(article["candidate_url"]) != 1):
        raise RuntimeError("POST_EVIDENCE_NOT_READY")
    # All Fallcent comparison prices MUST come from the exact product/item
    # detail with visible labelled evidence. A representative search image is
    # NOT acceptable price proof and must never be substituted for one.
    caption = row.get("caption", {})
    basis = caption.get("comparison_reference_source")
    verify_lowchart_mean_in_article(caption, article["body"])
    lowchart_other_basis = {
        "lowchart_observed_high": ("observed_high_krw", "💸 로우차트 관측 최고가"),
        "lowchart_observed_current": ("observed_current_krw", "💸 로우차트 관측 현재가"),
        "lowchart_observed_low": ("observed_low_krw", "💸 로우차트 관측 최저가"),
    }
    if basis in lowchart_other_basis:
        from gh_only.default_deal_format import validated_lowchart_observation
        field, label = lowchart_other_basis[basis]
        ids = row.get("product_identity", {})
        price = validated_lowchart_observation(
            row.get("history"), ids.get("product_id"), ids.get("item_id"),
            row.get("lowchart_product_title") or
            row.get("caption", {}).get("product_name"), field
        )
        if (type(price) is not int
                or price != caption.get("comparison_reference_price_krw")
                or f"{label} : 약 {price:,}원" not in article["body"]):
            raise RuntimeError("LOWCHART_VISIBLE_PRICE_EVIDENCE_MISMATCH")
    if basis == "fallcent_similar_search_current":
        # User permits a FAMILY-level option-unchecked comparison. It is a
        # relative price GAP only, not the actual item's discount/normal price.
        from gh_only.fallcent_similar_current import valid_search_price
        from gh_only.default_deal_format import create_default_preview
        proof = row.get("fallcent_similar_price_lookup")
        query = row.get("fallcent_similar_query_title")
        shown = valid_search_price(proof, query)
        price = caption.get("comparison_reference_price_krw")
        sale = caption.get("sale_price_krw")
        if (shown is None or price != shown or type(sale) is not int):
            raise RuntimeError("FALLCENT_SIMILAR_SEARCH_PRICE_DISCLOSURE_MISMATCH")
        expected = create_default_preview(
            sale, caption.get("product_name"), shown,
            row.get("source_message_id"),
            reference_source="fallcent_similar_search_current",
        )
        guarded_fields = (
            "approx_discount_vs_reference_pct",
            "reference_discount_under_one_pct",
            "similar_reference_price_gap_pct",
            "comparison_is_similar_product_only",
            "comparison_price_is_historical_average",
            "actual_discount_vs_reference_pct",
            "actual_discount_vs_historical_mean_pct",
            "approx_discount_vs_historical_mean_pct",
            "observed_average_krw",
            "fallcent_observed_average_krw",
        )
        # Recompute against the actual selected search-card price, so a
        # fabricated/larger gap cannot be injected into either publisher.
        if (any(caption.get(k) != expected.get(k) for k in guarded_fields)
                or caption.get("comparison_reference_source") !=
                   "fallcent_similar_search_current"
                or f"💸 폴센트 유사 상품 현재가(참고) : 약 {price:,}원"
                   not in article["body"]
                or not all(
                    phrase in article["body"]
                    for phrase in expected["caption"].split("\n\n")
                )
                or "용량·수량·옵션 및 실제 구매가는 다를 수 있습니다."
                   not in article["body"]):
            raise RuntimeError("FALLCENT_SIMILAR_SEARCH_PRICE_DISCLOSURE_MISMATCH")
    fallcent_basis = {
        "fallcent_observed_average", "fallcent_observed_high",
        "fallcent_high_low_midpoint", "fallcent_observed_current",
        "fallcent_observed_low",
    }
    if basis == "naver_general_search_first_shopping_card":
        raise RuntimeError("NAVER_PRICE_FALLBACK_DISABLED")
    if basis in fallcent_basis:
        proof = row.get("fallcent_high_lookup", {})
        ids = row.get("product_identity", {})
        source = urlsplit(proof.get("source_url") or "")
        from gh_only.fallcent_high_reference import exact_item_title_compatible
        verified = (
            proof.get("status") in ("HIGH_FOUND", "FALLCENT_HIGH_NOT_VISIBLE")
            and proof.get("product_id") == ids.get("product_id")
            and proof.get("item_id") == ids.get("item_id")
            and exact_item_title_compatible(
                row.get("caption", {}).get("product_name"),
                proof.get("source_title")
            )
            and source.scheme == "https"
            and source.hostname in ("fallcent.com", "www.fallcent.com")
            and source.path.startswith("/product/")
        )
        sale = caption.get("sale_price_krw")
        price = caption.get("comparison_reference_price_krw")
        high, low, current = (proof.get("observed_high_krw"),
                              proof.get("observed_low_krw"),
                              proof.get("observed_current_krw"))
        observed_average = proof.get("observed_average_krw")
        average_ok = (proof.get("average_basis") ==
                          "fallcent_public_visible_observed_average"
                      and type(observed_average) is int
                      and 100 <= observed_average <= 100_000_000)
        high_ok = (proof.get("status") == "HIGH_FOUND"
                   and proof.get("price_basis") ==
                       "fallcent_public_visible_historical_high"
                   and type(high) is int and 100 <= high <= 100_000_000)
        low_ok = (proof.get("low_basis") ==
                      "fallcent_public_visible_historical_low"
                  and type(low) is int and 100 <= low <= 100_000_000)
        current_ok = (proof.get("current_basis") ==
                          "fallcent_public_visible_current"
                      and type(current) is int and 100 <= current <= 100_000_000)
        expected = {
            "fallcent_observed_average": (
                observed_average if average_ok else None,
                "💸 쿠팡 평소 판매가(폴센트 관측 평균)"
            ),
            "fallcent_observed_high": (
                high if high_ok else None, "💸 폴센트 관측 최고가"
            ),
            "fallcent_high_low_midpoint": (
                (high + low) // 2 if high_ok and low_ok and high >= low
                else None, "💸 폴센트 최고·최저 중간값"
            ),
            "fallcent_observed_current": (
                current if current_ok else None, "💸 폴센트 관측 현재가"
            ),
            "fallcent_observed_low": (
                low if low_ok else None, "💸 폴센트 관측 최저가"
            ),
        }
        value, display_label = expected[basis]
        if (not verified or type(price) is not int or type(sale) is not int
                or price != value or
                f"{display_label} : 약 {price:,}원" not in article["body"]
                or caption.get("observed_average_krw") is not None
                or (caption.get("fallcent_observed_average_krw") != price
                    if basis == "fallcent_observed_average"
                    else caption.get("fallcent_observed_average_krw") is not None)):
            raise RuntimeError("FALLCENT_PRICE_EVIDENCE_MISMATCH")
    from urllib.parse import parse_qs
    link = urlsplit(article["candidate_url"])
    qs = parse_qs(link.query)
    ids = row["product_identity"]
    receipt = row.get("official_link_receipt")
    if receipt is not None:
        if (str(receipt.get("telegram_message_id")) != str(expected_id)
                or receipt.get("partners_link") != article["candidate_url"]
                or receipt.get("product_id") != ids.get("product_id")
                or receipt.get("item_id") != ids.get("item_id")
                or (receipt.get("vendor_item_id") is not None
                    and receipt.get("vendor_item_id") != ids.get("vendor_item_id"))):
            raise RuntimeError("OFFICIAL_LINK_RECEIPT_PRODUCT_MISMATCH")
    elif muse_postedit_mode():
        from gh_only.direct_product_links import verify_regular_product_url
        if (not row.get("muse_rewrite_pending") is True
                or not verify_regular_product_url(article["candidate_url"], ids)):
            raise RuntimeError("DIRECT_PRODUCT_AWAITING_MUSE_URL_MISMATCH")
    elif official_link_mode():
        raise RuntimeError("OFFICIAL_LINK_RECEIPT_REQUIRED")
    elif (qs.get("lptag") != [os.getenv("COUPANG_PARTNER_LPTAG")]
            or qs.get("pageKey") != [ids.get("product_id")]
            or qs.get("itemId") != [ids.get("item_id")]
            or (qs.get("subid") != [os.getenv("COUPANG_PARTNER_SUBID")]
                if os.getenv("COUPANG_PARTNER_SUBID") else "subid" in qs)):
        raise RuntimeError("OWNER_LINK_ITEM_OR_TAG_MISMATCH")
    return {**article, "product_id": ids["product_id"],
            "item_id": ids["item_id"],
            "vendor_item_id": ids.get("vendor_item_id")}


def build_next(s: dict) -> dict:
    from gh_only.default_deal_automation import enrich_deal
    validate_state(s)
    if s["in_flight"] is not None or not s["pending"]:
        raise RuntimeError("PENDING_ITEM_NOT_AVAILABLE")
    row = s["pending"][0]
    if muse_postedit_mode() and official_link_mode():
        raise RuntimeError("MUSE_POSTEDIT_AND_OFFICIAL_GATE_MUTUALLY_EXCLUSIVE")
    receipt = None
    if official_link_mode():
        receipt = load_receipt(row)
        if receipt is None:
            raise RuntimeError("OFFICIAL_LINK_RECEIPT_REQUIRED")
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
    if muse_postedit_mode():
        from gh_only.direct_product_links import regular_product_url
        direct = regular_product_url(candidate.get("product_identity", {}))
        prior = candidate.get("composed_affiliate_url")
        caption = candidate.get("caption", {}).get("caption", "")
        if not isinstance(prior, str) or caption.count(prior) != 1:
            raise RuntimeError("MUSE_POSTEDIT_PRODUCT_LINK_REPLACEMENT_FAILED")
        candidate["caption"]["caption"] = caption.replace(prior, direct, 1)
        candidate["composed_affiliate_url"] = direct
        candidate["muse_rewrite_pending"] = True
        candidate["affsdp"] = {
            "status": "REGULAR_PRODUCT_AWAITING_MUSE",
            "official_link_issued": False,
            "commission_attribution_verified": False,
        }
    if os.getenv("DAANGN_OFFICIAL_API_LINK_ENABLED") == "true":
        # This path is used only by the restored Daangn workflow. Never
        # publish locally fabricated AFFSDP links; issue the exact final URL
        # with no optional subId and never modify the API-returned link.
        from gh_only.coupang_partners_api import create_product_deeplink
        if receipt is not None or muse_postedit_mode():
            raise RuntimeError("DAANGN_OFFICIAL_API_MODE_CONFLICT")
        identity = candidate.get("product_identity", {})
        prior = candidate.get("composed_affiliate_url")
        caption = candidate.get("caption", {}).get("caption")
        if (not isinstance(prior, str)
                or not prior.startswith("https://link.coupang.com/re/AFFSDP?")
                or not isinstance(caption, str) or caption.count(prior) != 1
                or identity.get("status") != "EXACT_ITEM_IDENTIFIED"):
            raise RuntimeError("DAANGN_OFFICIAL_API_PRECONDITION_FAILED")
        issued = create_product_deeplink(identity)
        if (issued.get("subid_requested") is not None
                or issued.get("item_id") != identity.get("item_id")
                or issued.get("product_id") != identity.get("product_id")):
            raise RuntimeError("DAANGN_OFFICIAL_API_ITEM_MISMATCH")
        receipt = {
            "issued_by": "COUPANG_PARTNERS_API", "status": "LINK_READY",
            "telegram_message_id": row["id"],
            "partners_link": issued["partners_link"],
            "product_id": identity["product_id"],
            "item_id": identity["item_id"],
            "vendor_item_id": identity.get("vendor_item_id"),
            "subid_requested": None,
        }
    if receipt is not None:
        identity = candidate.get("product_identity", {})
        if (identity.get("status") != "EXACT_ITEM_IDENTIFIED"
                or identity.get("product_id") != receipt["product_id"]
                or identity.get("item_id") != receipt["item_id"]
                or (receipt.get("vendor_item_id") is not None
                    and identity.get("vendor_item_id") != receipt["vendor_item_id"])):
            raise RuntimeError("OFFICIAL_LINK_RECEIPT_PRODUCT_MISMATCH")
        prior = candidate.get("composed_affiliate_url")
        caption = candidate.get("caption", {}).get("caption", "")
        if not isinstance(prior, str) or caption.count(prior) != 1:
            raise RuntimeError("OFFICIAL_LINK_REPLACEMENT_FAILED")
        candidate["caption"]["caption"] = caption.replace(prior, receipt["partners_link"], 1)
        candidate["composed_affiliate_url"] = receipt["partners_link"]
        candidate["official_link_receipt"] = receipt
        candidate["affsdp"] = {
            "status": "OFFICIAL_PARTNERS_LINK_RECEIPT",
            "official_link_issued": True,
            "commission_attribution_verified": False,
        }
    (DRAFT_DIR / "drafts.json").write_text(json.dumps({
        "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
        "drafts": [candidate],
    }, ensure_ascii=False, indent=2) + "\n", "utf-8")
    article = validate_draft(DRAFT_DIR, row["id"])
    article = {**article, "posted_at_utc": row.get("posted_at_utc"),
               "manual_replay_approved": row.get("manual_replay_approved") is True}
    if busan_cafe_mode():
        caption = candidate.get("caption", {})
        article["manual_replay_approved"] = False
        article["busan_price_evidence"] = {
            "display_pct": caption.get("approx_discount_vs_reference_pct"),
            "reference_source": caption.get("comparison_reference_source"),
            "reference": caption.get("comparison_reference_price_krw"),
            "sale": caption.get("sale_price_krw"),
            "similar_only": caption.get("comparison_is_similar_product_only"),
        }
    return article


BUSAN_VERIFIED_PRICE_BASES = frozenset((
    "lowchart_public_history", "lowchart_observed_high",
    "lowchart_observed_current", "lowchart_observed_low",
    "fallcent_observed_high", "fallcent_observed_average",
    "fallcent_observed_current", "fallcent_observed_low",
    "fallcent_high_low_midpoint",
))


def busan_discount_eligible(article: dict) -> bool:
    proof = article.get("busan_price_evidence")
    if not isinstance(proof, dict):
        return False
    pct, sale, reference = (proof.get(k) for k in ("display_pct", "sale", "reference"))
    title = article.get("title")
    return bool(
        type(pct) is int and 30 <= pct <= 95
        and type(sale) is int and type(reference) is int
        and 0 < sale < reference <= 100_000_000
        and isinstance(proof.get("reference_source"), str)
        and proof.get("reference_source") in BUSAN_VERIFIED_PRICE_BASES
        and proof.get("similar_only") is not True
        and (reference - sale) * 100 >= reference * 30
        and isinstance(title, str) and title.endswith(f" [{pct}%할인]")
    )


def await_busan_skip_checkpoint(store, source_id: int, receipt: dict,
                                *, sleeper=None) -> dict:
    """Read-only barrier after a non-publishing skip; never repeat a PUT.

    GitHub Contents GET may temporarily show the old queue after PUT.
    Continue only when this exact skip receipt is visible and the item is
    no longer pending. Conflicting or unresolved state stops the job safely.
    """
    sleeper = sleeper or time.sleep
    key = str(source_id)
    for attempt in range(PUBLISHED_CHECKPOINT_VISIBILITY_ATTEMPTS):
        state, _ = store.read()
        if state["in_flight"] is not None or key in state["published"]:
            raise RuntimeError("BUSAN_SKIP_CHECKPOINT_CONFLICT_NO_POST")
        if any(key in state.get(bucket, {}) for bucket in
               ("skipped_expired", "skipped_unverifiable")):
            raise RuntimeError("BUSAN_SKIP_CHECKPOINT_CONFLICT_NO_POST")
        saved = state.get("skipped_busan_discount", {}).get(key)
        ids = [row["id"] for row in state["pending"]]
        if saved is not None:
            if saved != receipt or source_id in ids:
                raise RuntimeError("BUSAN_SKIP_CHECKPOINT_CONFLICT_NO_POST")
            print("BUSAN_SKIP_CHECKPOINT_VISIBLE source_id=" + key)
            return state
        # Only the old, still-unclaimed head is consistent with a stale read.
        if not ids or ids[0] != source_id:
            raise RuntimeError("BUSAN_SKIP_CHECKPOINT_CONFLICT_NO_POST")
        if attempt + 1 < PUBLISHED_CHECKPOINT_VISIBILITY_ATTEMPTS:
            print("BUSAN_SKIP_CHECKPOINT_WAIT source_id=" + key
                  + " attempt=" + str(attempt + 1))
            sleeper(1)
    raise RuntimeError("BUSAN_SKIP_CHECKPOINT_NOT_YET_VISIBLE_NO_POST")


def skip_busan_discount(store, source_id: int, article: dict, *, sleeper=None) -> dict:
    if (not busan_cafe_mode() or type(source_id) is not int
            or article.get("source_id") != source_id
            or busan_discount_eligible(article)):
        raise RuntimeError("BUSAN_SKIP_REQUEST_INVALID")
    state, sha = store.read()
    key = str(source_id)
    proof = article.get("busan_price_evidence")
    proof = proof if isinstance(proof, dict) else {}
    pct = proof.get("display_pct")
    basis = proof.get("reference_source")
    pct = pct if type(pct) is int else None
    basis = basis if isinstance(basis, str) else None
    if state["in_flight"] is not None or key in state["published"]:
        raise RuntimeError("BUSAN_DISCOUNT_QUEUE_CHANGED")
    saved = state.get("skipped_busan_discount", {}).get(key)
    if saved is not None:
        # Idempotent only for the same documented decision. Never overwrite
        # an existing receipt or remove another queue row.
        if (saved.get("status") != "NOT_VERIFIED_OVER_30"
                or saved.get("display_pct") != pct
                or saved.get("reference_source") != basis
                or any(row["id"] == source_id for row in state["pending"])):
            raise RuntimeError("BUSAN_SKIP_CHECKPOINT_CONFLICT_NO_POST")
        print("BUSAN_SKIP_ALREADY_RECORDED source_id=" + key)
        return state
    if (not state["pending"] or state["pending"][0]["id"] != source_id
            or any(key in state.get(bucket, {}) for bucket in
                   ("skipped_expired", "skipped_unverifiable"))):
        raise RuntimeError("BUSAN_DISCOUNT_QUEUE_CHANGED")
    # Retain the historical audit status for compatibility with existing FIFO records.
    # The eligibility check now uses the inclusive 30% threshold.
    receipt = {"status": "NOT_VERIFIED_OVER_30", "display_pct": pct,
               "reference_source": basis, "at": now_utc()}
    state["pending"].pop(0)
    state.setdefault("skipped_busan_discount", {})[key] = receipt
    store.write(state, sha, "state: Busan cafe skip deal not verified at least 30pct [skip ci]")
    print("BUSAN_DISCOUNT_SKIPPED source_id=" + key)
    return await_busan_skip_checkpoint(store, source_id, receipt, sleeper=sleeper)


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
        submitted_row = state["pending"].pop(0)
        state["published"][str(source_id)] = {
            "status": ("PUBLISHED_AWAITING_MUSE"
                       if outcome.get("muse_rewrite_pending") is True
                       else "PUBLISHED_VERIFIED"),
            "post_url": outcome["post_url"],
            "posted_at_utc": submitted_row.get("posted_at_utc"),
            "product_id": outcome.get("product_id"),
            "item_id": outcome.get("item_id"),
            "vendor_item_id": outcome.get("vendor_item_id"),
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


def await_published_checkpoint(store, source_id: int, run_id: str,
                               expected_url: str, *, sleeper=None) -> dict:
    """Wait for the verified persisted receipt; do not click Publish again.

    A successful GitHub PUT may not immediately appear in Contents GET.
    While the old pre-submit claim is still visible, retry READ ONLY.
    Never treat an unrelated/contradictory claim as a normal propagation lag.
    """
    if type(source_id) is not int or not run_id or not isinstance(expected_url, str):
        raise ValueError("PUBLISHED_CHECKPOINT_EXPECTATION_INVALID")
    sleeper = sleeper or time.sleep
    for attempt in range(PUBLISHED_CHECKPOINT_VISIBILITY_ATTEMPTS):
        state, _ = store.read()
        published = state["published"].get(str(source_id))
        flight = state["in_flight"]
        if published is not None:
            if (published.get("post_url") == expected_url
                    and published.get("run_id") == run_id
                    and flight is None
                    and all(row["id"] != source_id for row in state["pending"])):
                print("JIRUM_PUBLISHED_CHECKPOINT_VISIBLE source_id=" + str(source_id))
                return state
            raise RuntimeError("DAANGN_PUBLISHED_RECEIPT_CONFLICT_NO_REPOST")
        if flight is None or (flight.get("id") != source_id
                              or flight.get("run_id") != run_id):
            raise RuntimeError("DAANGN_CLAIM_CHANGED_NO_REPOST")
        if attempt + 1 < PUBLISHED_CHECKPOINT_VISIBILITY_ATTEMPTS:
            print("JIRUM_CHECKPOINT_READ_VISIBILITY_WAIT source_id=" +
                  str(source_id) + " attempt=" + str(attempt + 1))
            sleeper(1)
    raise RuntimeError("DAANGN_CHECKPOINT_NOT_YET_VISIBLE_NO_REPOST")


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
    discount_skipped = 0
    verified_checkpoint_state = None
    skip_receipts = {}
    published_receipts = {}
    while True:
        if verified_checkpoint_state is not None:
            # Use the confirmed snapshot once; do not rebuild a skipped deal
            # because another immediately subsequent GET was stale.
            state, verified_checkpoint_state = verified_checkpoint_state, None
        else:
            state, _ = store.read()
        # An already confirmed publication may briefly regress to its old
        # claim in a later Contents GET. Never build or submit from that view.
        for sid, url in published_receipts.items():
            receipt = state["published"].get(str(sid))
            if (receipt is None or receipt.get("run_id") != run_id
                    or receipt.get("post_url") != url
                    or any(row["id"] == sid for row in state["pending"])):
                state = await_published_checkpoint(store, sid, run_id, url)
        if any(state["published"].get(str(sid), {}).get("run_id") != run_id
               or state["published"].get(str(sid), {}).get("post_url") != url
               or any(row["id"] == sid for row in state["pending"])
               for sid, url in published_receipts.items()):
            raise RuntimeError("DAANGN_PUBLISHED_CHECKPOINT_REGRESSED_NO_REPOST")
        if skip_receipts:
            # A later read must not regress behind any skip in this drain.
            # Poll read-only on lag; never re-enrich, re-write, or submit it.
            for skipped_id, receipt in skip_receipts.items():
                if state.get("skipped_busan_discount", {}).get(str(skipped_id)) != receipt:
                    state = await_busan_skip_checkpoint(store, skipped_id, receipt)
            if any(state.get("skipped_busan_discount", {}).get(str(sid)) != receipt
                   for sid, receipt in skip_receipts.items()):
                raise RuntimeError("BUSAN_SKIP_CHECKPOINT_REGRESSED_NO_POST")
        if any(state["published"].get(str(sid), {}).get("run_id") != run_id
               or state["published"].get(str(sid), {}).get("post_url") != url
               or any(row["id"] == sid for row in state["pending"])
               for sid, url in published_receipts.items()):
            raise RuntimeError("DAANGN_PUBLISHED_CHECKPOINT_REGRESSED_NO_REPOST")
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
        if state["pending"] and awaiting_source_timestamp(state["pending"][0]):
            print("JIRUM_FIFO_AWAIT_SOURCE_TIMESTAMP " + json.dumps({
                "source_id": state["pending"][0]["id"],
                "repost_attempted": False,
            }))
            return 0
        if not state["pending"]:
            print("JIRUM_FIFO_DRAIN_COMPLETE " + json.dumps({
                "published_this_run": completed,
                "pending": 0,
                "reason": "QUEUE_EMPTY",
            }))
            return 0
        # Check budget only BETWEEN posts; never cut off mid-submission.
        if drain and (completed or discount_skipped) and clock() - started >= DRAIN_TIME_BUDGET_SECONDS:
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
                    if (permanent == "PHOTO_EVIDENCE_UNAVAILABLE"
                            and defer_transient_lowchart_photo(
                                store, source_id, DRAFT_DIR)):
                        # Wait for a later scheduled run / shared exact-item
                        # bundle instead of permanently skipping on one IP 403.
                        return 0
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
        if busan_cafe_mode() and not busan_discount_eligible(article):
            skipped_id = article["source_id"]
            verified_checkpoint_state = skip_busan_discount(store, skipped_id, article)
            skip_receipts[skipped_id] = verified_checkpoint_state["skipped_busan_discount"][str(skipped_id)]
            discount_skipped += 1
            continue
        if enforcing_freshness() and freshness_status(state["pending"][0]) != "FRESH":
            latest, sha = store.read()
            pruned, skipped = expire_pending(latest)
            if skipped:
                store.write(pruned, sha, "state: skip deal expired during draft preparation")
            continue
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
        if outcome.get("status") == "POST_PUBLISHED_AND_FORMAT_VERIFIED":
            outcome["muse_rewrite_pending"] = bool(article.get("muse_rewrite_pending"))
            outcome["product_id"] = article.get("product_id")
            outcome["item_id"] = article.get("item_id")
            outcome["vendor_item_id"] = article.get("vendor_item_id")
        # Save safe public-only publisher evidence before a flaky GitHub API
        # checkpoint. The existing Actions artifact captures it after failure.
        try:
            DRAFT_DIR.mkdir(parents=True, exist_ok=True)
            receipt = {
                "source_id": source_id, "run_id": run_id,
                "status": outcome.get("status"), "post_url": outcome.get("post_url"),
                "submit_attempted": outcome.get("submit_attempted"),
                "captured_at": now_utc(),
            }
            (DRAFT_DIR / "publish_outcome_receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print("JIRUM_LOCAL_PUBLISH_RECEIPT_SAVED source_id=" + str(source_id))
        except OSError:
            print("::warning::Unable to save publisher outcome receipt")
        # If checkpoint is still unconfirmed, the claim is quarantined later,
        # never replayed. Do not click the submit button twice.
        finish(store, source_id, run_id, outcome)
        if (outcome.get("status") == "POST_PUBLISHED_AND_FORMAT_VERIFIED"
                and outcome.get("post_url")):
            verified_checkpoint_state = await_published_checkpoint(
                store, source_id, run_id, outcome["post_url"]
            )
            published_receipts[source_id] = outcome["post_url"]
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


def scan_with_source_wait(store, expected_source_id=0, *, scanner=None, sleeper=None):
    """Allow an MTProto event to reach Telegram's public page before draining.

    No extra GitHub dispatches, browser installs or posts. Each read uses the
    existing scanner and durable cursor. Backup cron handles longer outages.
    """
    if type(expected_source_id) is not int or not 0 <= expected_source_id < 2**31:
        raise ValueError("INVALID_EXPECTED_SOURCE_ID")
    scanner = scanner or scan
    sleeper = sleeper or time.sleep
    for attempt in range(4):
        result = scanner(store)
        if (not expected_source_id or result.get("status") == "REVIEW_REQUIRED"
                or (result.get("last_scanned_id", 0) >= expected_source_id
                    and result.get("status") != "WAIT_SOURCE_TIMESTAMP")):
            return result
        if attempt < 3:
            print("JIRUM_WAIT_PUBLIC_SOURCE " + json.dumps({
                "expected_source_id": expected_source_id, "retry_in_seconds": 20,
            }))
            sleeper(20)
    print("JIRUM_PUBLIC_SOURCE_DELAY_BACKUP_REQUIRED " + str(expected_source_id))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("scan", "publish", "drain"))
    args = parser.parse_args()
    check_run_trigger()
    store = GithubState()
    if args.command == "scan":
        expected = int(os.getenv("JIRUM_EXPECTED_SOURCE_ID") or "0")
        result = scan_with_source_wait(
            store, expected,
            scanner=lambda current: scan(
                current, timestamp_fetcher=fetch_exact_source_post
            ),
        )
        output = os.getenv("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as f:
                f.write("has_deal=" + ("true" if result.get("has_deal") else "false") + "\n")
        return 0
    return publish_pending(store, drain=args.command == "drain")


if __name__ == "__main__":
    raise SystemExit(main())
