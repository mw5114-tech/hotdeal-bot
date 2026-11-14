"""Persistence for the *independent* public-product radar.

Writes ONLY state/api_radar_snapshot.json on the api-radar-data branch.
The Telegram, Daangn and Naver queues remain untouched. Never stores auth,
cookies, browser data, user identifiers or raw API responses.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib import error, parse, request

from api_radar.collector import MAX_PRODUCTS, check_state, empty_state, update_state, safe_url

REPOSITORY = "mw5114-tech/hotdeal-bot"
STATE_BRANCH = "api-radar-data"
STATE_PATH = "state/api_radar_snapshot.json"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/contents/{STATE_PATH}"
MAX_STATE_BYTES = 850_000


class RadarStateError(RuntimeError):
    """Safe error code only; never includes response JSON or secrets."""


def api(method: str, data: dict | None = None, *, url: str = API_URL) -> dict | None:
    if not url.startswith(f"https://api.github.com/repos/{REPOSITORY}/contents/"):
        raise RadarStateError("STATE_URL_NOT_ALLOWED")
    token = os.getenv("API_RADAR_GITHUB_TOKEN", "")
    if not token or len(token) < 20:
        raise RadarStateError("STATE_TOKEN_MISSING")
    body = json.dumps(data, separators=(",", ":")).encode() if data is not None else None
    req = request.Request(
        url, data=body, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            if response.status not in (200, 201):
                raise RadarStateError("STATE_HTTP_UNEXPECTED")
            raw = response.read(MAX_STATE_BYTES * 2 + 1)
    except error.HTTPError as exc:
        if method == "GET" and exc.code == 404:
            return None
        if exc.code in (409, 422):
            raise RadarStateError("STATE_CONFLICT_SAFE_STOP") from None
        raise RadarStateError("STATE_HTTP_" + str(exc.code)) from None
    except (error.URLError, TimeoutError, OSError):
        raise RadarStateError("STATE_NETWORK_FAILED") from None
    if len(raw) > MAX_STATE_BYTES * 2:
        raise RadarStateError("STATE_RESPONSE_TOO_BIG")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise RadarStateError("STATE_RESPONSE_INVALID") from None


def load_state() -> tuple[dict, str | None]:
    obj = api("GET", url=API_URL + "?ref=" + parse.quote(STATE_BRANCH))
    if obj is None:
        return empty_state(), None
    if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
        raise RadarStateError("STATE_METADATA_INVALID")
    try:
        raw = base64.b64decode(obj["content"])
        if len(raw) > MAX_STATE_BYTES:
            raise RadarStateError("STATE_TOO_BIG")
        saved = check_state(json.loads(raw))
    except (TypeError, ValueError, KeyError):
        raise RadarStateError("STATE_DECODE_INVALID") from None
    return saved, obj["sha"]


def save_state(state: dict, sha: str | None) -> None:
    raw = (json.dumps(check_state(state), ensure_ascii=False,
                      separators=(",", ":")) + "\n").encode()
    if len(raw) > MAX_STATE_BYTES:
        raise RadarStateError("STATE_TOO_BIG")
    content = base64.b64encode(raw).decode("ascii")
    body = {"message": "state: API radar public product observations [skip ci]",
            "branch": STATE_BRANCH, "content": content}
    if sha:
        body["sha"] = sha
    result = api("PUT", body)
    if not isinstance(result, dict) or "commit" not in result:
        raise RadarStateError("STATE_WRITE_UNCONFIRMED")


def persist_report(outdir: Path = Path("api-radar-output")) -> None:
    source = outdir / "summary.json"
    if not source.is_file():
        raise RadarStateError("RADAR_SCAN_SUMMARY_NOT_FOUND")
    summary = json.loads(source.read_text())
    if (not isinstance(summary, dict) or summary.get("schema") != "api-radar-readonly-v1"
            or summary.get("mode") != "scan"):
        raise RadarStateError("RADAR_REPORT_INVALID")
    observations = summary.get("all_observations")
    if not isinstance(observations, list) or len(observations) > MAX_PRODUCTS:
        raise RadarStateError("RADAR_OBSERVATIONS_INVALID")
    # Empty scans must not overwrite valid history or mark old products stale.
    if not observations:
        print("API_RADAR_NO_OBSERVATIONS_NO_STATE_WRITE")
        return
    for p in observations:
        if (not isinstance(p, dict) or
                set(p) != {"id", "name", "price", "source", "is_rocket", "free_shipping"}
                or not isinstance(p["id"], str)
                or not p["id"].isdigit() or len(p["id"]) > 18
                or type(p["price"]) is not int
                or not 1000 <= p["price"] <= 4_000_000):
            raise RadarStateError("RADAR_UNSAFE_OBSERVATION")
    metadata = summary.get("product_metadata", [])
    if not isinstance(metadata, list) or len(metadata) > MAX_PRODUCTS:
        raise RadarStateError("RADAR_METADATA_INVALID")
    validated = {}
    for row in metadata:
        if (not isinstance(row, dict) or set(row) != {"id", "url", "image", "category", "price"}
                or not isinstance(row["id"], str) or not row["id"].isdigit()
                or type(row["price"]) is not int
                or not safe_url(row["url"]) or not safe_url(row["image"], image=True)
                or not isinstance(row["category"], str) or len(row["category"]) > 60):
            raise RadarStateError("RADAR_METADATA_UNSAFE")
        validated[row["id"]] = row
    state, sha = load_state()
    scan_now = datetime.now(timezone.utc)
    updated, top = update_state(state, observations, scan_now)
    # Keep a CURRENT-DAY Goldbox inventory separate from product-history rows.
    # The generic keyword search must never overwrite a Goldbox membership
    # and a new day's inventory must never silently republish an old product.
    # Successful Goldbox snapshots made throughout one KST day are unioned.
    from datetime import timedelta
    day = scan_now.astimezone(timezone(timedelta(hours=9))).date().isoformat()
    goldbox = updated.get("goldbox_today")
    if not isinstance(goldbox, dict) or goldbox.get("kst_date") != day:
        goldbox = {"kst_date": day, "updated_at": None, "items": {}}
    if not isinstance(goldbox.get("items"), dict):
        raise RadarStateError("GOLDBOX_QUEUE_SCHEMA_INVALID")
    for row in observations:
        if row["source"] != "goldbox":
            continue
        meta = validated.get(row["id"])
        if not meta or meta["price"] != row["price"]:
            continue
        goldbox["items"][row["id"]] = {
            "name": row["name"], "price": row["price"],
            "category": meta["category"], "url": meta["url"],
            "image": meta["image"], "source": "goldbox",
            "checked_at": summary.get("checked_at_utc"),
        }
        goldbox["updated_at"] = summary.get("checked_at_utc")
    updated["goldbox_today"] = goldbox
    for row in observations:
        meta = validated.get(row["id"])
        if meta and meta["price"] == row["price"] and row["id"] in updated["products"]:
            updated["products"][row["id"]].update({
                "last_product_url": meta["url"],
                "last_image_url": meta["image"],
                "category": meta["category"],
                "last_checked_at_utc": summary.get("checked_at_utc"),
            })
    # No blog publish, no claim, no Naver browser even if a price drops a lot.
    safe_top = [{key: p[key] for key in (
        "id", "name", "price", "source", "score",
        "price_observation_days", "history_span_days",
        "observed_product_id_median_krw", "product_id_price_drop_pct",
        "posting_eligible", "reason")} for p in top]
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ranked.json").write_text(json.dumps({
        "schema": "api-radar-rank-v1",
        "scan_count": len(observations),
        "search_intent_verified": False,
        "option_identity_verified": False,
        "posts_published": 0,
        "ranked_candidates": safe_top,
        "disclaimer": "ProductId-level prices are not same-option historical prices."
    }, ensure_ascii=False, indent=2) + "\n")
    save_state(updated, sha)
    print("API_RADAR_PERSISTED", json.dumps({
        "observations": len(observations),
        "tracked_products": len(updated["products"]),
        "candidates": len(safe_top), "posts": 0,
        "branch": STATE_BRANCH,
    }))


if __name__ == "__main__":
    try:
        persist_report()
    except (RadarStateError, ValueError) as exc:
        print("API_RADAR_STATE_STOP", str(exc) if isinstance(exc, RadarStateError)
              else "STATE_VALIDATION_FAILED")
        raise SystemExit(1) from None
