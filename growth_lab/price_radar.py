"""Independent category-best option-level price-drop radar (NO blog posting).

Two category calls/run, with conservative multi-day option price history.
Publishes only public-product price facts to a separate state file.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import statistics
from urllib import error, parse, request

from api_radar.collector import HOST, PREFIX, MAX_RESPONSE, RadarApiError, signed_header, extract_rows, normalize
from api_radar.quality import extract_exact_identity
from growth_lab.github_store import load, save

KST = timezone(timedelta(hours=9))
PATH = "state/growth_lab_category_prices.json"
SCHEMA = "growth-category-prices-v1"
CATEGORIES = {
    "1016": "가전디지털", "1014": "생활용품",
    "1013": "주방용품", "1029": "반려동물",
    "1012": "식품", "1015": "홈인테리어",
}
MAX_ITEMS = 900
MAX_EVENTS = 150


class PriceRadarError(RuntimeError):
    pass


def category_path(category_id: str, limit: int = 30) -> str:
    if category_id not in CATEGORIES or type(limit) is not int or not 1 <= limit <= 100:
        raise PriceRadarError("BEST_CATEGORY_PARAMETERS_INVALID")
    return PREFIX + "/products/bestcategories/" + category_id + "?limit=" + str(limit)


def fetch_category(category_id: str, *, opener=None) -> list[dict]:
    path = category_path(category_id)
    access = os.getenv("COUPANG_PARTNERS_ACCESS_KEY", "")
    secret = os.getenv("COUPANG_PARTNERS_SECRET_KEY", "")
    if not access or not secret:
        raise PriceRadarError("BEST_PARTNERS_KEYS_MISSING")
    req = request.Request(
        HOST + path, method="GET",
        headers={"Authorization": signed_header(access, secret, path),
                 "Accept": "application/json",
                 "User-Agent": "CategoryPriceRadar/1.0"})
    try:
        with (opener or request.urlopen)(req, timeout=17) as response:
            if response.status != 200:
                raise PriceRadarError("BEST_HTTP_UNEXPECTED")
            raw = response.read(MAX_RESPONSE + 1)
    except error.HTTPError as exc:
        if exc.code == 429:
            raise PriceRadarError("BEST_RATE_LIMIT") from None
        raise PriceRadarError("BEST_HTTP_" + str(exc.code)) from None
    except (error.URLError, TimeoutError, OSError):
        raise PriceRadarError("BEST_NETWORK_FAILED") from None
    if len(raw) > MAX_RESPONSE:
        raise PriceRadarError("BEST_TOO_LARGE")
    try:
        body = json.loads(raw)
    except (UnicodeError, ValueError):
        raise PriceRadarError("BEST_INVALID_JSON") from None
    if not isinstance(body, dict) or str(body.get("rCode")) != "0":
        raise PriceRadarError("BEST_API_NON_SUCCESS")
    return extract_rows(body)


def categories_for_run(now: datetime) -> list[str]:
    """Rotate across six categories; 2 API calls per run, 2 runs daily."""
    if now.tzinfo is None:
        raise PriceRadarError("BEST_TIMEZONE_REQUIRED")
    day = now.astimezone(KST).date().toordinal()
    hour = now.astimezone(KST).hour
    window = 0 if hour < 15 else 1
    keys = list(CATEGORIES)
    start = ((day * 2 + window) * 2) % len(keys)
    return [keys[start], keys[(start + 1) % len(keys)]]


def candidate(row: dict, category_id: str) -> dict | None:
    product = normalize(row, "category_best")
    if not product:
        return None
    identity = extract_exact_identity(product["url"], product["id"])
    if not identity:
        return None  # productId alone is not an option-specific price identity
    return {"key": product["id"] + ":" + identity["item_id"],
            "id": product["id"], "item_id": identity["item_id"],
            "name": product["name"], "url": product["url"],
            "image": product["image"], "price": product["price"],
            "category": CATEGORIES[category_id],
            "category_id": category_id,
            "rocket": product["is_rocket"]}


def calculate_drop(history: list[dict], price: int, today: str) -> dict | None:
    """Uses >=3 distinct *prior* days spanning >=4 days; never MSRP."""
    valid = [h for h in history if isinstance(h, dict)
             and isinstance(h.get("day"), str) and h["day"] < today
             and type(h.get("price")) is int and h["price"] > 0]
    if len(valid) < 3:
        return None
    dates = sorted(set(x["day"] for x in valid))
    if len(dates) < 3 or (datetime.fromisoformat(dates[-1]) -
                           datetime.fromisoformat(dates[0])).days < 4:
        return None
    baseline = statistics.median([x["price"] for x in valid])
    drop = round((baseline - price) * 100 / baseline, 1)
    if not 20 <= drop <= 70:
        return None
    return {"prior_median_krw": int(baseline), "drop_vs_prior_pct": drop,
            "observation_days": len(dates),
            "prior_span_days": (datetime.fromisoformat(dates[-1]) -
                                datetime.fromisoformat(dates[0])).days,
            "comparison_kind": "same_option_observed_daily_prior_median"}


def merge(state: dict, offers: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    if state.get("schema") != SCHEMA or not isinstance(state.get("items"), dict):
        raise PriceRadarError("BEST_STATE_INVALID")
    today = now.astimezone(KST).date().isoformat()
    events = []
    for offer in offers:
        key = offer["key"]
        old = state["items"].get(key, {})
        hist = old.get("history", [])
        if not isinstance(hist, list):
            raise PriceRadarError("BEST_ITEM_HISTORY_INVALID")
        # Same item/option, KST calendar-day price snapshot.
        hist = [r for r in hist if isinstance(r, dict)
                and isinstance(r.get("day"), str) and r["day"] <= today
                and (datetime.fromisoformat(today) -
                     datetime.fromisoformat(r["day"])).days <= 45
                and type(r.get("price")) is int and r["price"] > 0]
        signal = calculate_drop(hist, offer["price"], today)
        previous = old.get("last_alert_price")
        if signal and (type(previous) is not int or
                       offer["price"] <= previous * .95):
            events.append({**{k: offer[k] for k in
                       ("key", "id", "item_id", "name", "category", "price", "url")},
                           **signal, "day": today})
            old["last_alert_price"] = offer["price"]
        hist = [h for h in hist if h["day"] != today]
        hist.append({"day": today, "price": offer["price"]})
        state["items"][key] = {**old, "name": offer["name"],
                              "category": offer["category"],
                              "category_id": offer["category_id"],
                              "last_price": offer["price"],
                              "last_url": offer["url"], "last_image": offer["image"],
                              "last_checked_at": now.isoformat(),
                              "history": hist[-45:]}
    if len(state["items"]) > MAX_ITEMS:
        keys = sorted(state["items"], key=lambda k:
                      state["items"][k].get("last_checked_at", ""), reverse=True)
        state["items"] = {k:state["items"][k] for k in keys[:MAX_ITEMS]}
    old_events = state.get("events", [])
    if not isinstance(old_events, list):
        raise PriceRadarError("BEST_EVENTS_INVALID")
    # Avoid duplicate event keys across retries and different windows.
    seen = {(x.get("key"), x.get("day"), x.get("price"))
            for x in old_events if isinstance(x, dict)}
    fresh = [x for x in events if (x["key"], x["day"], x["price"]) not in seen]
    state["events"] = (old_events + fresh)[-MAX_EVENTS:]
    state["updated_at"] = now.isoformat()
    return state, fresh


def run(*, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    ids = categories_for_run(now)
    offers = []
    for category in ids:
        rows = fetch_category(category)
        offers.extend(x for r in rows if (x := candidate(r, category)))
    old, sha = load(PATH, schema=SCHEMA)
    state = old or {"schema": SCHEMA, "items": {}, "events": []}
    merged, events = merge(state, offers, now)
    save(PATH, merged, sha, schema=SCHEMA)
    # Public product prices only, no personal affiliate data.
    print("GROWTH_PRICE_RADAR_OK categories=" + ",".join(ids) +
          " exact_option_offers=" + str(len(offers)) +
          " new_validated_drop_events=" + str(len(events)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (PriceRadarError, RadarApiError) as exc:
        print("GROWTH_PRICE_RADAR_STOP " + str(exc))
        raise SystemExit(1) from None
