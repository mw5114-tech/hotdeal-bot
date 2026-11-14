"""Independent GitHub Contents persistence for Growth Lab only.

The repo is PUBLIC. Only public product data or encrypted sales data belongs
under these allowlisted paths. Secrets and plaintext reports must never be
committed, printed or placed in an unencrypted artifact.
"""
from __future__ import annotations

import base64
import json
import os
from urllib import error, parse, request

REPO = "mw5114-tech/hotdeal-bot"
BRANCH = "api-radar-data"
PREFIX = "state/growth_lab_"
MAX_BYTES = 850_000


class GrowthStoreError(RuntimeError):
    pass


def _url(path: str) -> str:
    if not isinstance(path, str) or not path.startswith(PREFIX):
        raise GrowthStoreError("GROWTH_STATE_PATH_NOT_ALLOWED")
    suffix = path[len(PREFIX):]
    if not suffix or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-." for c in suffix):
        raise GrowthStoreError("GROWTH_STATE_PATH_INVALID")
    return "https://api.github.com/repos/" + REPO + "/contents/" + path


def _call(method: str, path: str, body: dict | None = None) -> dict | None:
    token = os.getenv("GROWTH_LAB_GITHUB_TOKEN", "")
    if len(token) < 20:
        raise GrowthStoreError("GROWTH_GITHUB_TOKEN_MISSING")
    raw = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    req = request.Request(
        _url(path) + ("?ref=" + BRANCH if method == "GET" else ""),
        data=raw, method=method, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "CoupangGrowthLab/1.0"})
    try:
        with request.urlopen(req, timeout=20) as response:
            if response.status not in (200, 201):
                raise GrowthStoreError("GROWTH_GITHUB_STATUS")
            data = response.read(MAX_BYTES * 2 + 1)
    except error.HTTPError as exc:
        if method == "GET" and exc.code == 404:
            return None
        if exc.code in (409, 422):
            raise GrowthStoreError("GROWTH_GITHUB_CONFLICT_RETRY_NEXT_RUN") from None
        raise GrowthStoreError("GROWTH_GITHUB_HTTP_" + str(exc.code)) from None
    except (error.URLError, TimeoutError, OSError):
        raise GrowthStoreError("GROWTH_GITHUB_NETWORK_FAILED") from None
    if len(data) > MAX_BYTES * 2:
        raise GrowthStoreError("GROWTH_GITHUB_RESPONSE_TOO_LARGE")
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise GrowthStoreError("GROWTH_GITHUB_RESPONSE_INVALID") from None


def load(path: str, *, schema: str) -> tuple[dict | None, str | None]:
    result = _call("GET", path)
    if result is None:
        return None, None
    if not isinstance(result, dict) or not isinstance(result.get("sha"), str):
        raise GrowthStoreError("GROWTH_STATE_METADATA_INVALID")
    try:
        raw = base64.b64decode(result["content"], validate=False)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        obj = json.loads(raw)
    except (KeyError, TypeError, ValueError):
        raise GrowthStoreError("GROWTH_STATE_DECODE_INVALID") from None
    if not isinstance(obj, dict) or obj.get("schema") != schema:
        raise GrowthStoreError("GROWTH_STATE_SCHEMA_MISMATCH")
    return obj, result["sha"]


def save(path: str, obj: dict, sha: str | None, *, schema: str) -> None:
    if not isinstance(obj, dict) or obj.get("schema") != schema:
        raise GrowthStoreError("GROWTH_STATE_SCHEMA_INVALID")
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_BYTES:
        raise GrowthStoreError("GROWTH_STATE_TOO_LARGE")
    body = {"message": "growth-lab: update " + path.rsplit("/", 1)[-1],
            "content": base64.b64encode(raw).decode(),
            "branch": BRANCH}
    if sha:
        body["sha"] = sha
    _call("PUT", path, body)
