"""Optional, official NAVER API HUB Shopping Insight signal.

Topic-level relative shopping clicks are NOT absolute search volume and do not
prove traffic to an individual product or blog post. Fail closed on missing
keys, unexpected response, API error, or weak/flat trend. No secrets in logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from urllib import error, request

API_URL = "https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords"
MAX_BYTES = 120_000
KST = timezone(timedelta(hours=9))
# A curated category choice, NOT inferred from untrusted product strings.
TOPICS = {
    "가습기": "50000003", "무선 키보드": "50000003",
    "기계식 키보드": "50000003", "외장 SSD": "50000003",
    "보조배터리": "50000003", "모니터암": "50000003",
    "무선청소기": "50000003", "에어프라이어": "50000003",
    "전기포트": "50000003", "전기요": "50000008",
    "캡슐세제": "50000008", "커피캡슐": "50000006",
}


class TrendError(RuntimeError):
    """Fixed status only, never include request body or any key."""


def matching_topic(title: str) -> tuple[str, str] | None:
    if not isinstance(title, str):
        return None
    normalized = " ".join(title.lower().split())
    # Longest keyword wins, avoiding overlapping keyboard topics.
    for word in sorted(TOPICS, key=len, reverse=True):
        if word.lower() in normalized:
            return (word, TOPICS[word])
    return None


def trend_payload(keyword: str, category: str, now: datetime) -> dict:
    if TOPICS.get(keyword) != category:
        raise TrendError("UNRECOGNIZED_TOPIC_CATEGORY")
    yesterday = now.astimezone(KST).date() - timedelta(days=1)
    return {
        "startDate": (yesterday - timedelta(days=27)).isoformat(),
        "endDate": yesterday.isoformat(),
        "timeUnit": "date",
        "category": category,
        "keyword": [{"name": keyword, "param": [keyword]}],
    }


def parse_trend(data: object, keyword: str) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise TrendError("TREND_RESPONSE_INVALID")
    matching = [x for x in data["results"] if isinstance(x, dict)
                and x.get("title") == keyword]
    if len(matching) != 1:
        raise TrendError("TREND_RESULT_MISMATCH")
    points = matching[0].get("data")
    if not isinstance(points, list) or len(points) < 21:
        raise TrendError("TREND_INSUFFICIENT_HISTORY")
    byday = {}
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get("period"), str):
            continue
        ratio = point.get("ratio")
        if (type(ratio) in (float, int) and 0 <= ratio <= 100
                and len(point["period"]) == 10):
            byday[point["period"]] = float(ratio)
    ordered = [byday[x] for x in sorted(byday)]
    if len(ordered) < 21:
        raise TrendError("TREND_INSUFFICIENT_VALID_DAYS")
    start = sum(ordered[:7]) / 7
    recent = sum(ordered[-7:]) / 7
    growth = round(recent / start, 3) if start >= 1 else None
    # Avoid treating tiny/near-zero series as significant demand.
    eligible = bool(growth is not None and growth >= 1.10 and recent >= 15)
    return {
        "verified": True,
        "keyword": keyword,
        "data_type": "relative_shopping_click_index",
        "recent_seven_day_mean": round(recent, 2),
        "first_seven_day_mean": round(start, 2),
        "growth_ratio": growth,
        "demand_uptrend": eligible,
        "not_absolute_search_count": True,
    }


def fetch_topic_trend(keyword: str, category: str, *, now=None, opener=None) -> dict:
    key_id = os.getenv("NAVER_API_HUB_KEY_ID", "").strip()
    secret = os.getenv("NAVER_API_HUB_KEY", "").strip()
    if not key_id or not secret:
        raise TrendError("NAVER_HUB_KEYS_MISSING")
    if len(key_id) > 256 or len(secret) > 256 or any(x in key_id + secret for x in "\r\n"):
        raise TrendError("NAVER_HUB_KEYS_INVALID")
    body = json.dumps(trend_payload(keyword, category, now or datetime.now(timezone.utc)),
                      separators=(",", ":")).encode("utf-8")
    req = request.Request(API_URL, data=body, method="POST", headers={
        "X-NCP-APIGW-API-KEY-ID": key_id,
        "X-NCP-APIGW-API-KEY": secret,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with (opener or request.urlopen)(req, timeout=12) as response:
            if response.status != 200:
                raise TrendError("TREND_HTTP_UNEXPECTED")
            raw = response.read(MAX_BYTES + 1)
    except error.HTTPError as exc:
        raise TrendError("TREND_HTTP_" + str(exc.code)) from None
    except (error.URLError, TimeoutError, OSError):
        raise TrendError("TREND_NETWORK_ERROR") from None
    if len(raw) > MAX_BYTES:
        raise TrendError("TREND_RESPONSE_OVERSIZE")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise TrendError("TREND_JSON_INVALID") from None
    return parse_trend(payload, keyword)
