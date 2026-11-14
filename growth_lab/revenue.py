"""Daily Coupang Partners analytics. NEVER writes plaintext earnings to public GitHub.

Fetches read-only authenticated reporting endpoints, computes date/subid
summaries, encrypts with an HKDF-derived key from the existing Partners
secret, and persists only AES-GCM ciphertext on api-radar-data.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib import error, parse, request

from api_radar.collector import HOST, PREFIX, signed_header
from growth_lab.github_store import load, save

KST = timezone(timedelta(hours=9))
PATH = "state/growth_lab_private_analytics.json"
SCHEMA = "growth-revenue-encrypted-v1"
CIPHER_SCHEMA = "growth-revenue-ciphertext-v1"
REPORT_TYPES = ("clicks", "orders", "cancels", "commission")
FIELD_MAP = {
    "clicks": ("clickCount", "clicks", "click"),
    "orders": ("orderCount", "orders", "order"),
    "cancels": ("cancelCount", "cancels", "cancel"),
    "commission": ("commission", "commissionAmount", "totalCommission"),
}
MAX_RESPONSE = 250_000


class RevenueError(RuntimeError):
    pass


def report_path(kind: str, day: str, page: int = 0) -> str:
    if kind not in REPORT_TYPES or not (len(day) == 8 and day.isdecimal()):
        raise RevenueError("REPORT_PARAMETERS_INVALID")
    if type(page) is not int or not 0 <= page <= 3:
        raise RevenueError("REPORT_PAGE_INVALID")
    return (PREFIX + "/v1/reports/" + kind + "?" +
            parse.urlencode({"startDate": day, "endDate": day, "page": page}))


def fetch_report(kind: str, day: str, page: int = 0, *, opener=None) -> list[dict]:
    path = report_path(kind, day, page)
    key = os.getenv("COUPANG_PARTNERS_ACCESS_KEY", "")
    secret = os.getenv("COUPANG_PARTNERS_SECRET_KEY", "")
    if not key or not secret:
        raise RevenueError("PARTNERS_KEYS_MISSING")
    req = request.Request(HOST + path, method="GET",
        headers={"Authorization": signed_header(key, secret, path),
                 "Accept": "application/json",
                 "User-Agent": "GrowthPrivateReport/1.0"})
    try:
        with (opener or request.urlopen)(req, timeout=18) as response:
            if response.status != 200:
                raise RevenueError("REPORT_HTTP_UNEXPECTED")
            body = response.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        if exc.code == 429:
            raise RevenueError("REPORT_RATE_LIMIT") from None
        raise RevenueError("REPORT_HTTP_" + str(exc.code)) from None
    except (error.URLError, OSError, TimeoutError):
        raise RevenueError("REPORT_NETWORK") from None
    if len(body) > MAX_RESPONSE:
        raise RevenueError("REPORT_RESPONSE_OVERSIZE")
    try:
        obj = json.loads(body)
    except (UnicodeError, ValueError):
        raise RevenueError("REPORT_INVALID_JSON") from None
    if not isinstance(obj, dict) or str(obj.get("rCode", "0")) != "0":
        raise RevenueError("REPORT_NON_SUCCESS_" + kind.upper())
    rows = obj.get("data")
    if isinstance(rows, dict):
        rows = rows.get("content", rows.get("items", rows.get("reports", [])))
    if not isinstance(rows, list):
        raise RevenueError("REPORT_DATA_SHAPE_" + kind.upper())
    if len(rows) > 500:
        raise RevenueError("REPORT_PAGE_TOO_LARGE")
    return [r for r in rows if isinstance(r, dict)]


def value_for(row: dict, kind: str) -> float | None:
    for key in FIELD_MAP[kind]:
        raw = row.get(key)
        if type(raw) in (int, float) and not isinstance(raw, bool) and 0 <= raw <= 1e12:
            return float(raw)
        if isinstance(raw, str):
            try:
                val = float(raw.replace(",", ""))
                if 0 <= val <= 1e12:
                    return val
            except ValueError:
                pass
    return None


def summarize(rows_by_kind: dict[str, list[dict]], day: str) -> dict:
    """No invented product attribution: group only on visible API dimensions."""
    kinds = {}
    for kind in REPORT_TYPES:
        rows = rows_by_kind.get(kind, [])
        sums = {}
        unknown = 0
        for row in rows:
            value = value_for(row, kind)
            if value is None:
                unknown += 1
                continue
            dimension = (row.get("subId") or row.get("subid")
                         or row.get("categoryName") or "UNSPECIFIED")
            if not isinstance(dimension, str) or len(dimension) > 90:
                dimension = "UNSPECIFIED"
            sums[dimension] = round(sums.get(dimension, 0.0) + value, 4)
        kinds[kind] = {"segments": sums, "rows": len(rows),
                       "unmapped_rows": unknown}
    return {"date": day, "reports": kinds,
            "attribution_level": "API_returned_group_only",
            "per_blog_post_attribution_verified": False}


def _aead():
    try:
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RevenueError("CRYPTOGRAPHY_PACKAGE_REQUIRED") from None
    secret = os.getenv("COUPANG_PARTNERS_SECRET_KEY", "")
    if len(secret) < 20:
        raise RevenueError("REPORT_ENCRYPTION_SECRET_MISSING")
    key = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=b"GrowthLab-v1-2026-private-report",
               info=b"independent-revenue-state-encryption").derive(secret.encode())
    return AESGCM(key)


def encrypt_report(obj: dict) -> dict:
    nonce = os.urandom(12)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > 300_000:
        raise RevenueError("REPORT_STATE_TOO_BIG")
    sealed = _aead().encrypt(nonce, raw, SCHEMA.encode())
    return {"schema": CIPHER_SCHEMA,
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(sealed).decode()}


def decrypt_report(obj: dict) -> dict:
    if obj.get("schema") != CIPHER_SCHEMA:
        raise RevenueError("REPORT_CIPHERTEXT_SCHEMA_INVALID")
    try:
        decoded = _aead().decrypt(base64.b64decode(obj["nonce"]),
                                  base64.b64decode(obj["ciphertext"]), SCHEMA.encode())
        result = json.loads(decoded)
        if not isinstance(result, dict) or result.get("schema") != SCHEMA:
            raise ValueError()
        return result
    except (KeyError, ValueError):
        raise RevenueError("REPORT_DECRYPTION_FAILED") from None


def run(*, day: str | None = None) -> int:
    day = day or (datetime.now(KST).date() - timedelta(days=1)).strftime("%Y%m%d")
    if len(day) != 8 or not day.isdecimal():
        raise RevenueError("REPORT_DATE_INVALID")
    fetched = {}
    for kind in REPORT_TYPES:
        rows = []
        # Default one page. If a full 500-row page arrives, try next page.
        # Never silently mark an incomplete report as final.
        for page in range(4):
            part = fetch_report(kind, day, page)
            rows.extend(part)
            if len(part) < 500:
                break
        else:
            raise RevenueError("REPORT_PAGINATION_NOT_COMPLETE")
        fetched[kind] = rows
    new = summarize(fetched, day)
    old_cipher, sha = load(PATH, schema=CIPHER_SCHEMA)
    old = (decrypt_report(old_cipher) if old_cipher else
           {"schema": SCHEMA, "daily": {}})
    previous = old.get("daily")
    if not isinstance(previous, dict):
        raise RevenueError("REPORT_HISTORY_INVALID")
    previous[day] = new
    for date in list(previous):
        if date < (datetime.strptime(day, "%Y%m%d") -
                   timedelta(days=45)).strftime("%Y%m%d"):
            del previous[date]
    old["updated_at"] = datetime.now(timezone.utc).isoformat()
    save(PATH, encrypt_report(old), sha, schema=CIPHER_SCHEMA)
    # Never expose actual revenue, click totals or personal order rows in
    # GitHub Actions logs or publicly accessible artifacts.
    print("GROWTH_REVENUE_SUCCESS date=" + day +
          " types=" + ",".join(REPORT_TYPES) + " storage=AES256GCM")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--day", default=None, help="YYYYMMDD UTC-compatible local reporting day")
    p.add_argument("--decrypt-file", default=None,
                   help="Offline owner's private copy of encrypted JSON; prints locally")
    args = p.parse_args()
    if args.decrypt_file:
        from pathlib import Path
        doc = json.loads(Path(args.decrypt_file).read_text())
        print(json.dumps(decrypt_report(doc), ensure_ascii=False, indent=2))
        return 0
    return run(day=args.day)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RevenueError as exc:
        print("GROWTH_REVENUE_STOP " + str(exc))
        raise SystemExit(1) from None
