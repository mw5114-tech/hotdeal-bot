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
    unusual = [str(c.get("domain", "")).lstrip(".").lower()
               for c in data["cookies"] if c.get("domain") not in ALLOWED]
    if unusual:
        if all(d == "kakao.com" or d.endswith(".kakao.com") for d in unusual):
            return "KAKAO_SUBDOMAIN_COOKIE_ONLY"
        if all(d == "daum.net" or d.endswith(".daum.net") or
               d == "kakao.com" or d.endswith(".kakao.com") for d in unusual):
            return "KAKAO_OR_DAUM_COOKIE_ONLY"
        return "UNRELATED_COOKIE_DOMAIN_PRESENT"
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
