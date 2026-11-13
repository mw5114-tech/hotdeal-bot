"""Isolated Coupang Partners product radar.

No Telegram access, Naver login, browser use, or blog posting.
Uses the owner's approved Partners API keys as read-only request credentials.
Never logs credentials, raw API responses, customer data, or cookies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import statistics
import sys
from urllib import error, parse, request

HOST = "https://api-gateway.coupang.com"
PREFIX = "/v2/providers/affiliate_open_api/apis/openapi"
GOLDBOX = PREFIX + "/products/goldbox"
SEARCH = PREFIX + "/products/search"
MAX_RESPONSE = 750_000
MAX_PRODUCTS = 110
MAX_TRACKED = 500
MAX_AGE_DAYS = 65
KEYWORDS = (
    "가습기", "전기요", "무선 키보드", "외장 SSD",
    "모니터암", "캡슐세제", "무선청소기", "에어프라이어",
    "커피캡슐", "보조배터리", "전기포트", "기계식 키보드",
)
BLACKLIST = ("중고", "리퍼", "병행수입", "랜덤", "복불복", "추가구성",
             "대여", "렌탈", "예약금", "케이스만", "호환용", "사은품")
MAX_RESULTS = 12


class RadarApiError(RuntimeError):
    """Error codes only; never contain URL, response body, or credentials."""


def signed_header(access: str, secret: str, path_query: str, clock=None) -> str:
    if not access or not secret or len(access) > 300 or len(secret) > 300:
        raise RadarApiError("API_CREDENTIAL_MISSING_OR_INVALID")
    if any(c in access + secret for c in "\n\r,"):
        raise RadarApiError("API_CREDENTIAL_INVALID_CHAR")
    signed_date = (clock or datetime.now(timezone.utc)).strftime("%y%m%dT%H%M%SZ")
    signature = hmac.new(
        secret.encode(), (signed_date + "GET" + path_query).encode(),
        hashlib.sha256,
    ).hexdigest()
    return ("CEA algorithm=HmacSHA256, access-key=" + access +
            ", signed-date=" + signed_date + ", signature=" + signature)


def get_api(path: str, query: dict | None = None, *, opener=None) -> dict:
    if path not in (GOLDBOX, SEARCH):
        raise RadarApiError("API_PATH_NOT_ALLOWED")
    if query and path != SEARCH:
        raise RadarApiError("API_QUERY_NOT_ALLOWED")
    if path == SEARCH:
        if not isinstance(query, dict) or set(query) != {"keyword", "limit"}:
            raise RadarApiError("API_SEARCH_QUERY_INVALID")
        if (not isinstance(query["keyword"], str) or
                not 1 <= len(query["keyword"]) <= 32 or query["limit"] != 10):
            raise RadarApiError("API_SEARCH_QUERY_INVALID")
    query_string = parse.urlencode(query) if query else ""
    path_query = path + ("?" + query_string if query_string else "")
    access = os.environ.get("COUPANG_PARTNERS_ACCESS_KEY", "")
    secret = os.environ.get("COUPANG_PARTNERS_SECRET_KEY", "")
    headers = {
        "Authorization": signed_header(access, secret, path_query),
        "Accept": "application/json",
        "User-Agent": "OwnerPartnersRadar/1.0",
    }
    req = request.Request(HOST + path_query, headers=headers, method="GET")
    try:
        with (opener or request.urlopen)(req, timeout=16) as response:
            if response.status != 200:
                raise RadarApiError("API_HTTP_NOT_200")
            raw = response.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        if exc.code == 429:
            raise RadarApiError("API_RATE_LIMIT_STOP") from None
        raise RadarApiError("API_HTTP_" + str(exc.code)) from None
    except (error.URLError, OSError, TimeoutError):
        raise RadarApiError("API_NETWORK_FAILURE") from None
    if len(raw) > MAX_RESPONSE:
        raise RadarApiError("API_RESPONSE_TOO_BIG")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RadarApiError("API_RESPONSE_INVALID_JSON") from None
    if not isinstance(obj, dict) or str(obj.get("rCode")) != "0":
        raise RadarApiError("API_APPLICATION_NON_SUCCESS")
    return obj


def extract_rows(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("productData", data.get("products", []))
    else:
        rows = []
    if not isinstance(rows, list):
        raise RadarApiError("API_DATA_SHAPE_INVALID")
    return [r for r in rows[:MAX_PRODUCTS] if isinstance(r, dict)]


def safe_url(value: str, *, image=False) -> bool:
    if not isinstance(value, str) or not 12 <= len(value) <= 1200:
        return False
    try:
        u = parse.urlsplit(value)
    except ValueError:
        return False
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.port is not None or u.fragment):
        return False
    if image:
        return (re.fullmatch(r"(?:thumbnail\d*|image\d*|img\d{1,2}[a-z]|t\d{1,2}[a-z])\.coupangcdn\.com", u.hostname) is not None
                and not u.path.endswith(".svg"))
    return (u.hostname in ("link.coupang.com", "coupa.ng", "www.coupang.com", "m.coupang.com")
            and u.path.startswith(("/a/", "/re/", "/vp/products/", "/np/")))


def normalize(row: dict, source: str) -> dict | None:
    raw_id = row.get("productId")
    if type(raw_id) is float and raw_id.is_integer():
        raw_id = int(raw_id)
    pid = str(raw_id)
    name = row.get("productName")
    raw_price = row.get("productPrice")
    price = int(raw_price) if type(raw_price) is float and raw_price.is_integer() else raw_price
    image = row.get("productImage")
    url = row.get("productUrl")
    if not (re.fullmatch(r"\d{5,18}", pid) and isinstance(name, str)
            and 6 <= len(name.strip()) <= 160 and len(name) < 500
            and type(price) is int and 1000 <= price <= 4_000_000
            and safe_url(image, image=True) and safe_url(url)
            and not any(bad in name for bad in BLACKLIST)):
        return None
    name = " ".join(name.split())[:150]
    # ProductId-only observations do not establish same-option price history.
    return {
        "id": pid,
        "name": name,
        "price": price,
        "image": image,
        "url": url,
        "source": source,
        "is_rocket": row.get("isRocket") is True,
        "free_shipping": row.get("isFreeShipping") is True,
        "category": str(row.get("categoryName") or "")[:60],
        "option_verified": False,
        "query_click_index": None,  # requires separately authorized Naver trend API
    }


def unique_products(products: list[dict]) -> list[dict]:
    values = {}
    for product in products:
        pid = product["id"]
        old = values.get(pid)
        if not old or (product["source"] == "goldbox" and old["source"] != "goldbox"):
            values[pid] = product
    return list(values.values())


def empty_state() -> dict:
    return {"schema": "api-radar-snapshot-v1", "updated_at": None,
            "products": {}, "published": {}}


def check_state(state: object) -> dict:
    if not isinstance(state, dict) or state.get("schema") != "api-radar-snapshot-v1":
        raise ValueError("RADAR_STATE_SCHEMA_INVALID")
    if not isinstance(state.get("products"), dict) or not isinstance(state.get("published"), dict):
        raise ValueError("RADAR_STATE_FIELDS_INVALID")
    return state


def assess(product: dict, history: list[dict], today: str) -> dict:
    # Each history point is one productId/day; identical item option is not
    # confirmed by the API, so even a large drop is just a review candidate.
    prior = [x for x in history if x.get("date") != today
             and type(x.get("price")) is int and x["price"] > 0]
    baseline = int(statistics.median([x["price"] for x in prior])) if len(prior) >= 5 else None
    span = ((datetime.fromisoformat(prior[-1]["date"]) -
             datetime.fromisoformat(prior[0]["date"])).days
            if len(prior) >= 5 else 0)
    drop = round((baseline - product["price"]) * 100 / baseline, 1) if baseline else None
    score = 0
    score += 20 if product["source"] == "goldbox" else 0
    score += 10 if product["is_rocket"] else 0
    score += 5 if product["free_shipping"] else 0
    score += 15 if 20_000 <= product["price"] <= 120_000 else 8
    score += 20 if drop is not None and drop >= 20 and span >= 7 else 0
    score += 10 if drop is not None and drop >= 30 and span >= 7 else 0
    return {**product, "score": score, "price_observation_days": len(prior),
            "history_span_days": span, "observed_product_id_median_krw": baseline,
            "product_id_price_drop_pct": drop,
            "posting_eligible": False,
            "reason": "OPTION_AND_SEARCH_INTENT_NOT_VERIFIED"}


def update_state(state: dict, products: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    state = check_state(state)
    today = now.astimezone(timezone.utc).date().isoformat()
    rows = state["products"]
    results = []
    for p in unique_products(products):
        old = rows.get(p["id"], {})
        history = old.get("history", []) if isinstance(old, dict) else []
        history = [x for x in history if isinstance(x, dict)
                   and isinstance(x.get("date"), str) and type(x.get("price")) is int
                   and x["date"] < today and
                   (now.date() - datetime.fromisoformat(x["date"]).date()).days <= MAX_AGE_DAYS]
        result = assess(p, history, today)
        results.append(result)
        history.append({"date": today, "price": p["price"]})
        # Store only public market data. Do not put credentials/cookies in Git.
        rows[p["id"]] = {"name": p["name"], "last_price": p["price"],
                         "last_source": p["source"], "history": history[-MAX_AGE_DAYS:]}
    # Bounded state, newest observations win.
    if len(rows) > MAX_TRACKED:
        ranked = sorted(rows, key=lambda k: rows[k].get("history", [{}])[-1].get("date", ""), reverse=True)
        state["products"] = {k: rows[k] for k in ranked[:MAX_TRACKED]}
    state["updated_at"] = now.isoformat()
    results.sort(key=lambda p: (-p["score"], p["price"], p["id"]))
    return state, results[:MAX_RESULTS]


def scan(*, probe=False, outdir=Path("api-radar-output")) -> int:
    now = datetime.now(timezone.utc)
    outdir.mkdir(parents=True, exist_ok=True)
    sources = []
    errors = []
    try:
        sources.extend((row, "goldbox") for row in extract_rows(get_api(GOLDBOX)))
    except RadarApiError as exc:
        errors.append(str(exc))
        if str(exc) == "API_RATE_LIMIT_STOP":
            return write_report(outdir, now, [], errors, probe)
    # Probe uses exactly 1 call; regular scans use at most 2 calls.
    if not probe and not errors:
        keyword = KEYWORDS[(now.toordinal() + now.hour // 12) % len(KEYWORDS)]
        try:
            sources.extend((row, "search") for row in extract_rows(
                get_api(SEARCH, {"keyword": keyword, "limit": 10})))
        except RadarApiError as exc:
            errors.append(str(exc))
    normalized = [x for raw, source in sources if (x := normalize(raw, source))]
    # Report only counts/types/field names, never raw product values or auth data.
    if sources:
        raw_rows = [row for row, _ in sources]
        first = raw_rows[0]
        print("API_RADAR_SCHEMA", json.dumps({
            "raw_rows": len(raw_rows),
            "normalized_rows": len(normalized),
            "field_names": sorted(str(k) for k in first.keys())[:24],
            "price_type": type(first.get("productPrice")).__name__,
            "image_hosts": sorted({parse.urlsplit(str(row.get("productImage", ""))).hostname
                                   for row in raw_rows if str(row.get("productImage", "")).startswith("https://")}),
            "image_url_allowed_count": sum(safe_url(row.get("productImage"), image=True) for row in raw_rows),
            "product_url_allowed_count": sum(safe_url(row.get("productUrl")) for row in raw_rows),
            "numeric_price_count": sum(type(row.get("productPrice")) in (int, float) and
                                      float(row.get("productPrice")).is_integer() for row in raw_rows),
            "id_types": sorted({type(row.get("productId")).__name__ for row in raw_rows}),
        }))
    return write_report(outdir, now, unique_products(normalized), errors, probe)


def write_report(outdir: Path, now: datetime, products: list[dict],
                 errors: list[str], probe: bool) -> int:
    # Sanitized public-product report only. Never writes Actions Secrets,
    # session data, raw API JSON, or any personal account info.
    payload = {
        "schema": "api-radar-readonly-v1",
        "checked_at_utc": now.isoformat(),
        "mode": "probe" if probe else "scan",
        "posting_enabled": False,
        "api_calls_max": 1 if probe else 2,
        "normalized_count": len(products),
        "candidates": [{k: p[k] for k in ("id", "name", "price", "source",
                                         "is_rocket", "free_shipping", "category")}
                       for p in products[:MAX_RESULTS]],
        "errors": errors,
        "notes": ["No blog posting, no price-history discount claim, no generated reviews.",
                  "ProductId does not establish option-level price identity."],
    }
    (outdir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print("API_RADAR", json.dumps({"mode": payload["mode"], "count": len(products),
                                   "errors": errors, "posts": 0, "api_calls_max": payload["api_calls_max"]}))
    return 1 if errors and not products else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true")
    p.add_argument("--out", default="api-radar-output")
    args = p.parse_args()
    try:
        raise SystemExit(scan(probe=args.probe, outdir=Path(args.out)))
    except (RadarApiError, ValueError) as exc:
        print("API_RADAR_STOP", type(exc).__name__)
        raise SystemExit(1) from None
