"""Bounded cross-channel reuse of VERIFIED Lowchart mean for one Telegram post.

Naver and Daangn use different GitHub runners. Lowchart sometimes returns 403
to one and 200 to the other. Share a small, immutable *public* exact-item
observation immediately after a successful read. Never trust another SKU, a
different Telegram post, a stale snapshot, a fabricated price, or a website
error response. No hidden Lowchart API, proxy, or CAPTCHA workaround.
No affiliate URLs, login cookies, source text or secrets are stored here.
Failure to read/write this optional aid MUST NOT prevent a normal post.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import time
from urllib import error, request

from gh_only.default_deal_format import validated_history
from gh_only.lowchart_price_reference import build_url

REPOSITORY = "mw5114-tech/hotdeal-bot"
SCHEMA = "verified-lowchart-average-for-telegram-source-v1"
PATH_PREFIX = "state/verified_lowchart_price"
MAX_AGE_SECONDS = 15 * 60
MAX_PAYLOAD = 8000
WAIT_SECONDS = (0, 5, 5, 5)  # only if history was not verified; 15s maximum
VISIBLE_KEYS = (
    "provider", "http_status", "source_url", "product_id", "item_id",
    "page_title", "observed_low_krw", "observed_high_krw",
    "observed_average_krw", "observed_current_krw", "history_window",
    "source_observation_type", "source_last_change_date",
    "reference_discount_vs_average_pct", "source_item_identifiers_present",
    "title_option_matches", "usable_historical_reference",
    "visible_exact_item_prices_verified", "official_normal_price", "reason",
)


def _path(source_id: int) -> str:
    if type(source_id) is not int or not 1 <= source_id < 2**31:
        raise ValueError("INVALID_LOWCHART_SHARED_SOURCE_ID")
    return f"{PATH_PREFIX}/{source_id}.json"


def _time(raw: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(raw)
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def create_record(source_id: int, history: dict, title: str) -> dict | None:
    """Only after *local* exact-item, visible mean verification."""
    try:
        _path(source_id)
        if not isinstance(history, dict):
            return None
        pid, iid = history.get("product_id"), history.get("item_id")
        if (not isinstance(pid, str) or not isinstance(iid, str)
                or history.get("source_url") != build_url(pid, iid)
                or history.get("http_status") != 200
                or validated_history(history, pid, iid, title) is None):
            return None
        record = {
            "schema": SCHEMA, "source_id": source_id,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "history": {k: history.get(k) for k in VISIBLE_KEYS},
        }
        if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_PAYLOAD:
            return None
        return record
    except (ValueError, TypeError):
        return None


def verified_history(record: dict, source_id: int,
                     product_id: str, item_id: str,
                     *, now: datetime | None = None) -> dict | None:
    """Validate source+SKU+window+price-order and immutable provenance."""
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        return None
    if record.get("source_id") != source_id:
        return None
    at = _time(record.get("observed_at_utc"))
    current = now or datetime.now(timezone.utc)
    if at is None or not -30 <= (current - at).total_seconds() <= MAX_AGE_SECONDS:
        return None
    h = record.get("history")
    if not isinstance(h, dict) or set(h) != set(VISIBLE_KEYS):
        return None
    if (h.get("provider") != "lowchart" or h.get("http_status") != 200
            or h.get("product_id") != product_id
            or h.get("item_id") != item_id
            or h.get("source_item_identifiers_present") is not True
            or h.get("title_option_matches") is not True
            or h.get("visible_exact_item_prices_verified") is not True
            or h.get("usable_historical_reference") is not True
            or h.get("official_normal_price") is not False):
        return None
    try:
        if h.get("source_url") != build_url(product_id, item_id):
            return None
    except (ValueError, TypeError):
        return None
    low, avg, high, observed = (
        h.get("observed_low_krw"), h.get("observed_average_krw"),
        h.get("observed_high_krw"), h.get("observed_current_krw")
    )
    if (any(type(x) is not int or not 0 < x < 100_000_000
            for x in (low, avg, high, observed))
            or not low <= avg <= high or not low <= observed <= high):
        return None
    title = h.get("page_title")
    if (not isinstance(title, str) or not title.strip()
            or validated_history(h, product_id, item_id, title) != avg):
        return None
    window = h.get("history_window")
    if (not isinstance(window, str)
            or not re.fullmatch(r"recent_([1-9]|[1-4]\d|5[0-2])_weeks|entire_tracked_history_unspecified_days", window)):
        return None
    return h


def _api(source_id: int, token: str, method: str, payload=None):
    path = _path(source_id)
    url = f"https://api.github.com/repos/{REPOSITORY}/contents/{path}"
    if method == "GET":
        url += "?ref=main"
    hdr = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token,
    }
    if payload is not None:
        hdr["Content-Type"] = "application/json"
    req = request.Request(
        url, headers=hdr, method=method,
        data=(json.dumps(payload).encode("utf-8") if payload is not None else None))
    with request.urlopen(req, timeout=8) as resp:
        if method == "GET" and resp.status != 200:
            return None
        if method == "PUT" and resp.status not in (200, 201):
            return None
        obj = json.loads(resp.read(16000))
        if not isinstance(obj, dict):
            return None
        return obj


def read_record(source_id: int, token: str) -> dict | None:
    if not token:
        return None
    try:
        obj = _api(source_id, token, "GET")
        if (not obj or obj.get("type") != "file"
                or not isinstance(obj.get("content"), str)):
            return None
        contents = base64.b64decode(obj["content"].replace("\n", ""), validate=True)
        if len(contents) > MAX_PAYLOAD:
            return None
        return json.loads(contents.decode("utf-8"))
    except (error.HTTPError, error.URLError, OSError, ValueError,
            TypeError, json.JSONDecodeError):
        return None


def write_record(source_id: int, record: dict, token: str) -> None:
    """Create only; never overwrite conflicting data or retry unknown writes."""
    if not token:
        return
    try:
        data = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) > MAX_PAYLOAD:
            return
        _api(source_id, token, "PUT", {
            "message": f"state: verified Lowchart average for {source_id} [skip ci]",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": "main",
        })
        print("LOWCHART_SHARED_PRICE_STORED source_id=" + str(source_id))
    except (error.HTTPError, error.URLError, OSError, ValueError, TypeError):
        # A sibling may have won the same source path, or the write response
        # may have been lost. Never overwrite or resubmit an uncertain PUT.
        print("LOWCHART_SHARED_PRICE_STORE_SKIPPED source_id=" + str(source_id))


def maybe_share_or_recover(source_id: int, product_id: str, item_id: str,
                           history: dict, current_title: str | None,
                           *, token: str | None = None, reader=None,
                           writer=None, sleeper=None) -> dict | None:
    """Best-effort supplement, never mutate input or alter posting gate.

    Return a verified sibling history only if local historical mean failed.
    Call ONLY for the normal live publisher with verified Coupang exact IDs.
    """
    if type(source_id) is not int:
        return None
    try:
        _path(source_id)
        build_url(product_id, item_id)
    except (ValueError, TypeError):
        return None
    token = token if token is not None else os.getenv("GH_STATE_TOKEN", "")
    if not token:
        return None
    reader = reader or read_record
    writer = writer or write_record
    sleeper = sleeper or time.sleep
    if create_record(source_id, history, current_title or "") is not None:
        record = create_record(source_id, history, current_title or "")
        try:
            writer(source_id, record, token)
        except Exception:
            print("LOWCHART_SHARED_PRICE_STORE_SKIPPED")
        return None
    for delay in WAIT_SECONDS:
        if delay:
            sleeper(delay)
        try:
            record = reader(source_id, token)
        except Exception:
            continue
        h = verified_history(record, source_id, product_id, item_id)
        if h is None:
            continue
        print("LOWCHART_SHARED_PRICE_RECOVERED source_id=" + str(source_id))
        return dict(h)
    print("LOWCHART_SHARED_PRICE_NOT_FOUND source_id=" + str(source_id))
    return None
