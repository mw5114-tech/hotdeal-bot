"""Coupang price-drop V2: same-option 6h observation + robust real drops.

No publisher or browser here. 2 authenticated category requests/run,
daily + intraday histories, and only fully evidenced S/A event records.
The independent V2 publisher rechecks a CURRENT API offer before posting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import statistics
from urllib import error, parse, request

from api_radar.collector import HOST, PREFIX, MAX_RESPONSE, RadarApiError, signed_header, extract_rows, normalize
from api_radar.quality import extract_exact_identity, title_tokens
from growth_lab.github_store import load, save

KST = timezone(timedelta(hours=9))
PATH = "state/growth_lab_category_prices.json"
SCHEMA = "growth-category-prices-v1"
CATEGORIES = {
    "1016": "가전디지털", "1014": "생활용품",
    "1013": "주방용품", "1029": "반려동물",
    "1012": "식품", "1015": "홈인테리어",
}
MAX_ITEMS = 280
MAX_EVENTS = 150
MAX_TICKS = 10
MAX_HISTORY_DAYS = 38
EVENT_FRESH_MINUTES = 120


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
    """Two official API calls every 2h; each of 6 categories ~every 6h."""
    if now.tzinfo is None:
        raise PriceRadarError("BEST_TIMEZONE_REQUIRED")
    hour = now.astimezone(KST).hour
    keys = list(CATEGORIES)
    start = ((hour // 2) % 3) * 2
    return [keys[start], keys[start + 1]]


def candidate(row: dict, category_id: str) -> dict | None:
    product = normalize(row, "category_best")
    if not product:
        return None
    identity = extract_exact_identity(product["url"], product["id"])
    if not identity:
        return None  # productId alone is not an option-specific price identity
    tokens = title_tokens(product["name"])
    if len(tokens) < 2:
        return None
    return {"key": product["id"] + ":" + identity["item_id"],
            "id": product["id"], "item_id": identity["item_id"],
            "vendor_item_id": identity.get("vendor_item_id"),
            "title_signature": "|".join(t.lower() for t in tokens[:2]),
            "name": product["name"], "url": product["url"],
            "image": product["image"], "price": product["price"],
            "category": CATEGORIES[category_id],
            "category_id": category_id,
            "rocket": product["is_rocket"],
            "free_shipping": product["free_shipping"]}


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


def classify_v2(history: list[dict], ticks: list[dict], price: int,
                now: datetime) -> dict | None:
    """No signal without both prior daily baseline AND a real ~24h observation.

    The prior-day median prevents an inflated single MSRP from creating a
    fake deal. Matching productId+itemId+vendor/title is checked by merge.
    """
    if type(price) is not int or price < 1000 or now.tzinfo is None:
        return None
    day = now.astimezone(KST).date().isoformat()
    cutoff = (now.astimezone(KST).date() - timedelta(days=30)).isoformat()
    daily = {h["day"]: h["price"] for h in history
             if isinstance(h, dict) and type(h.get("price")) is int
             and h["price"] > 0 and isinstance(h.get("day"), str)
             and cutoff <= h["day"] < day}
    dates = sorted(daily)
    if len(dates) < 5 or (datetime.fromisoformat(dates[-1]) -
                           datetime.fromisoformat(dates[0])).days < 7:
        return None
    median = int(statistics.median(daily.values()))
    drop_30 = round(100 * (median - price) / median, 1)
    if not 20 <= drop_30 <= 70:
        return None
    old = []
    for tick in ticks:
        if not isinstance(tick, dict) or type(tick.get("price")) is not int:
            continue
        stamp = tick.get("at")
        try:
            parsed = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            continue
        age_hours = (now - parsed).total_seconds() / 3600
        if 18 <= age_hours <= 31:
            old.append((abs(age_hours - 24), parsed, tick["price"]))
    if not old:
        return None
    previous = min(old, key=lambda x: (x[0], -x[1].timestamp()))[2]
    if previous <= 0:
        return None
    drop_24 = round(100 * (previous - price) / previous, 1)
    if not 15 <= drop_24 <= 70:
        return None
    grade = "S" if drop_24 >= 20 and drop_30 >= 25 else "A"
    return {
        "grade": grade, "prior_30d_median_krw": median,
        "price_24h_ago_krw": previous,
        "drop_vs_30d_median_pct": drop_30,
        "drop_vs_24h_pct": drop_24,
        "prior_daily_observation_days": len(dates),
        "prior_span_days": (datetime.fromisoformat(dates[-1]) -
                            datetime.fromisoformat(dates[0])).days,
        "comparison_kind": "same_option_prior_30d_daily_median_and_24h_observed",
    }


def eligible_events(state: dict, now: datetime, *, published: dict | None = None,
                    claims: dict | None = None) -> list[dict]:
    """Read-only preflight for time-limited events; no API/browser work."""
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or now.tzinfo is None:
        return []
    items = state.get("items", {})
    if not isinstance(items, dict):
        return []
    posted, claimed = published or {}, claims or {}
    found = []
    for event in state.get("events", [])[-MAX_EVENTS:]:
        if not isinstance(event, dict) or event.get("grade") not in ("S", "A"):
            continue
        try:
            stamp = datetime.fromisoformat(event["observed_at"])
            age = (now - stamp).total_seconds()
            if stamp.tzinfo is None or not 0 <= age <= EVENT_FRESH_MINUTES * 60:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        pid, key = event.get("id"), event.get("key")
        row = items.get(key)
        if (not isinstance(pid, str) or pid in posted or pid in claimed
                or not isinstance(row, dict) or row.get("last_price") != event.get("price")
                or row.get("last_checked_at") != event.get("observed_at")
                or row.get("category_id") != event.get("category_id")
                or row.get("vendor_item_id") != event.get("vendor_item_id")
                or row.get("title_signature") != event.get("title_signature")):
            continue
        if (type(event.get("price")) is not int or
                not 20 <= event.get("drop_vs_30d_median_pct", -999) <= 70 or
                not 15 <= event.get("drop_vs_24h_pct", -999) <= 70):
            continue
        found.append(event)
    return sorted(found, key=lambda e: (e["grade"] != "S",
                         -e["drop_vs_30d_median_pct"], -e["drop_vs_24h_pct"]))


def merge(state: dict, offers: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    if state.get("schema") != SCHEMA or not isinstance(state.get("items"), dict):
        raise PriceRadarError("BEST_STATE_INVALID")
    if now.tzinfo is None:
        raise PriceRadarError("BEST_TIMEZONE_REQUIRED")
    today = now.astimezone(KST).date().isoformat()
    now_iso = now.isoformat()
    events = []
    for offer in offers:
        key = offer["key"]
        old = state["items"].get(key, {})
        # An item ID alone should not silently mix seller/vendor or title
        # variant changes into a historical comparison.
        changed_vendor = (old.get("vendor_item_id") is not None
                          and offer.get("vendor_item_id") is not None
                          and old["vendor_item_id"] != offer["vendor_item_id"])
        changed_title = (bool(old.get("title_signature"))
                         and bool(offer.get("title_signature"))
                         and old["title_signature"] != offer["title_signature"])
        if changed_vendor or changed_title:
            old = {}
        hist = old.get("history", [])
        ticks = old.get("ticks", [])
        if not isinstance(hist, list) or not isinstance(ticks, list):
            raise PriceRadarError("BEST_ITEM_HISTORY_INVALID")
        hist = [h for h in hist if isinstance(h, dict)
                and isinstance(h.get("day"), str) and h["day"] <= today
                and (datetime.fromisoformat(today) -
                     datetime.fromisoformat(h["day"])).days <= MAX_HISTORY_DAYS
                and type(h.get("price")) is int and h["price"] > 0]
        tick_rows = []
        for tick in ticks:
            if not isinstance(tick, dict) or type(tick.get("price")) is not int:
                continue
            try:
                d = datetime.fromisoformat(tick.get("at"))
                age = (now - d).total_seconds()
                if d.tzinfo is not None and 0 <= age <= 40 * 3600:
                    tick_rows.append(tick)
            except (TypeError, ValueError):
                continue
        signal = classify_v2(hist, tick_rows, offer["price"], now)
        previous_alert = old.get("last_alert_price")
        if (signal and (type(previous_alert) is not int
                         or offer["price"] <= previous_alert * .95)):
            event = {**{k: offer.get(k) for k in (
                     "key", "id", "item_id", "vendor_item_id", "title_signature",
                     "name", "category", "category_id", "price", "url", "image",
                     "rocket", "free_shipping")},
                     **signal, "day": today, "observed_at": now_iso,
                     "source": "category_best"}
            events.append(event)
            old["last_alert_price"] = offer["price"]
        hist = [h for h in hist if h["day"] != today]
        hist.append({"day": today, "price": offer["price"]})
        tick_rows.append({"at": now_iso, "price": offer["price"]})
        state["items"][key] = {
            **old, "name": offer["name"], "category": offer["category"],
            "category_id": offer["category_id"], "item_id": offer["item_id"],
            "vendor_item_id": offer.get("vendor_item_id"),
            "title_signature": offer.get("title_signature"),
            "last_price": offer["price"], "last_url": offer["url"],
            "last_image": offer["image"], "last_checked_at": now_iso,
            "history": hist[-MAX_HISTORY_DAYS:],
            "ticks": tick_rows[-MAX_TICKS:],
        }
    if len(state["items"]) > MAX_ITEMS:
        keys = sorted(state["items"], key=lambda k:
                      state["items"][k].get("last_checked_at", ""), reverse=True)
        state["items"] = {k: state["items"][k] for k in keys[:MAX_ITEMS]}
    existing = state.get("events", [])
    if not isinstance(existing, list):
        raise PriceRadarError("BEST_EVENTS_INVALID")
    seen = {(e.get("key"), e.get("day"), e.get("price"))
            for e in existing if isinstance(e, dict)}
    fresh = [e for e in events
             if (e["key"], e["day"], e["price"]) not in seen]
    state["events"] = (existing + fresh)[-MAX_EVENTS:]
    state["updated_at"] = now_iso
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
    print("GROWTH_PRICE_V2_OK categories=" + ",".join(ids) +
          " exact_option_offers=" + str(len(offers)) +
          " new_validated_drop_events=" + str(len(events)) +
          " tracked_items=" + str(len(merged["items"])))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (PriceRadarError, RadarApiError) as exc:
        print("GROWTH_PRICE_RADAR_STOP " + str(exc))
        raise SystemExit(1) from None
