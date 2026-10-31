"""Classify a GitHub session JSON validation failure without exposing auth data.

Never print cookie values, domains, localStorage values, or the raw JSON.
Diagnostic only; does not initiate an authenticated connection or modify secrets.
"""
import json
import os
from urllib.parse import urlsplit

RAW = os.environ.get("KAKAO_DEV_STORAGE_STATE_JSON", "")
ALLOWED = {"kakao.com", ".kakao.com", "accounts.kakao.com", "developers.kakao.com"}
ALLOWED_ORIGINS = {"developers.kakao.com", "accounts.kakao.com"}

def classify(raw):
    if not raw:
        return "MISSING_SECRET"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return "INVALID_JSON"
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        return "NOT_PLAYWRIGHT_STORAGE_STATE"
    if any(not isinstance(c, dict) for c in data["cookies"]):
        return "INVALID_COOKIE_RECORD"
    if any(c.get("domain") not in ALLOWED for c in data["cookies"]):
        return "COOKIE_DOMAIN_WHITELIST_TOO_STRICT"
    for entry in data.get("origins", []):
        if not isinstance(entry, dict):
            return "INVALID_ORIGIN_RECORD"
        origin = entry.get("origin", "")
        if not isinstance(origin, str) or not origin.startswith("https://"):
            return "INVALID_ORIGIN_RECORD"
        if urlsplit(origin).hostname not in ALLOWED_ORIGINS:
            return "LOCAL_STORAGE_ORIGIN_WHITELIST_TOO_STRICT"
    return "PASSES_EXISTING_ALLOWLIST_CHECKS"

if __name__ == "__main__":
    print("KAKAO_SESSION_DIAGNOSTIC: " + classify(RAW))
