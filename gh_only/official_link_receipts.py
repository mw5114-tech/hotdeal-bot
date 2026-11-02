"""Validated official Coupang Partners link receipts from Muse.

Each links/<Telegram message id>.json is one official URL created in the
logged-in Partners UI. A receipt is metadata supplied by Muse, NOT independent
proof of affiliate attribution. Never use a fabricated AFFSDP candidate for
production publication.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

RECEIPT_ROOT = Path("links")
MAX_RECEIPT_BYTES = 4096
MAX_CLOCK_SKEW = timedelta(minutes=5)
LINK_PATH = re.compile(r"/a/[A-Za-z0-9]{6,80}\Z")
NUMBER = re.compile(r"[1-9][0-9]{4,20}\Z")
FIELDS = frozenset({
    "telegram_message_id", "telegram_posted_at", "product_id", "item_id",
    "vendor_item_id", "partners_link", "issued_at", "status",
})


def parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 45:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def official_url(raw: object) -> bool:
    if not isinstance(raw, str) or len(raw) > 250:
        return False
    try:
        url = urlsplit(raw)
    except ValueError:
        return False
    return (url.scheme == "https"
            and url.hostname == "link.coupang.com"
            and url.port is None and url.username is None
            and url.password is None and not url.query and not url.fragment
            and bool(LINK_PATH.fullmatch(url.path)))


def validate_receipt(data: object, row: dict, *, now: datetime | None = None) -> dict:
    """Fail closed on mismatched message, time, identity or link shape."""
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("OFFICIAL_LINK_RECEIPT_FIELDS_INVALID")
    mid = row.get("id")
    if type(mid) is not int or mid <= 0 or str(data.get("telegram_message_id")) != str(mid):
        raise ValueError("OFFICIAL_LINK_MESSAGE_ID_MISMATCH")
    if data.get("status") != "LINK_READY":
        raise ValueError("OFFICIAL_LINK_NOT_READY")
    posted = parse_timestamp(row.get("posted_at_utc"))
    supplied_posted = parse_timestamp(data.get("telegram_posted_at"))
    issued = parse_timestamp(data.get("issued_at"))
    current = now or datetime.now(timezone.utc)
    if posted is None or supplied_posted != posted or issued is None:
        raise ValueError("OFFICIAL_LINK_TIMESTAMP_INVALID_OR_MISMATCHED")
    if issued > current + MAX_CLOCK_SKEW or issued < posted - MAX_CLOCK_SKEW:
        raise ValueError("OFFICIAL_LINK_ISSUED_AT_INCONSISTENT")
    if not official_url(data.get("partners_link")):
        raise ValueError("OFFICIAL_LINK_URL_INVALID")
    if not all(isinstance(data.get(key), str) and NUMBER.fullmatch(data[key])
               for key in ("product_id", "item_id")):
        raise ValueError("OFFICIAL_LINK_PRODUCT_IDS_INVALID")
    vendor = data.get("vendor_item_id")
    if vendor is not None and (not isinstance(vendor, str) or not NUMBER.fullmatch(vendor)):
        raise ValueError("OFFICIAL_LINK_VENDOR_ID_INVALID")
    return data


def load_receipt(row: dict, root: Path = RECEIPT_ROOT, *, now=None) -> dict | None:
    """Read one file only; missing means waiting, invalid means blocked."""
    mid = row.get("id")
    if type(mid) is not int or mid <= 0:
        raise ValueError("OFFICIAL_LINK_MESSAGE_ID_INVALID")
    path = root / f"{mid}.json"
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("OFFICIAL_LINK_RECEIPT_TOO_LARGE")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("OFFICIAL_LINK_RECEIPT_BAD_JSON") from exc
    return validate_receipt(data, row, now=now)
