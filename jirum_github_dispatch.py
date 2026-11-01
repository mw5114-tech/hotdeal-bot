"""Optional event-driven @jirum -> GitHub Actions bridge.

The existing Telegram watcher and bot deliveries work unchanged when this
bridge is disabled. Only post IDs are persisted; no Telegram bodies or secret
tokens are copied to GitHub. Failed requests retain the pending signal for
a later retry. GitHub FIFO itself remains the publication source of truth.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from json import dumps
import os
import time
from urllib import error, request
from urllib.parse import urlsplit

REPO = "mw5114-tech/hotdeal-bot"
WORKFLOW = "daangn-jirum-manual-fifo.yml"
ENDPOINT = (
    "https://api.github.com/repos/" + REPO +
    "/actions/workflows/" + WORKFLOW + "/dispatches"
)
DEBOUNCE_SECONDS = 20
MIN_INTERVAL_SECONDS = 75
RETRY_SECONDS = 180
PENDING_KEY = "github_dispatch_pending_post"
SINCE_KEY = "github_dispatch_pending_since"
LAST_ACK_ID_KEY = "github_dispatch_ack_post"
LAST_SENT_KEY = "github_dispatch_last_sent_at"
RETRY_AT_KEY = "github_dispatch_retry_at"
COUPANG_HOSTS = frozenset({
    "link.coupang.com", "coupang.com", "www.coupang.com", "m.coupang.com"
})


def coupang_event(post: dict) -> bool:
    """Use exact approved Coupang hostnames; avoid false substring matches."""
    if type(post.get("id")) is not int or post["id"] <= 0:
        return False
    candidates = list(post.get("links") or [])
    if post.get("product_url"):
        candidates.append(post["product_url"])
    for value in candidates:
        try:
            parsed = urlsplit(value)
        except (TypeError, ValueError):
            continue
        if (parsed.scheme == "https" and
                parsed.hostname in COUPANG_HOSTS and
                not parsed.username and not parsed.password):
            return True
    return False


def workflow_dispatch(token: str, opener=None) -> None:
    """Call the official Actions write endpoint; never log the credential."""
    if not token or any(x.isspace() for x in token):
        raise RuntimeError("GITHUB_DISPATCH_TOKEN_INVALID")
    payload = dumps({"ref": "main"}).encode("utf-8")
    req = request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "JirumEventToActions/1.0",
        },
    )
    open_func = opener or request.urlopen
    with open_func(req, timeout=22) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError("DISPATCH_HTTP_NOT_ACCEPTED")


class GitHubEventBridge:
    """Persist a single coalesced signal in the existing watcher SQLite DB."""

    def __init__(self, store, token: str, *, clock=None, sender=None, logger=None):
        self.store = store
        self.token = token
        self.clock = clock or time.time
        self.sender = sender or workflow_dispatch
        self.logger = logger or (lambda code, **fields: None)
        self.wake = asyncio.Event()

    def observe(self, post: dict) -> bool:
        if not coupang_event(post):
            return False
        mid = post["id"]
        if mid <= (self.store.get(LAST_ACK_ID_KEY) or 0):
            return False
        existing = self.store.get(PENDING_KEY) or 0
        if mid <= existing:
            return False
        with self.store.db:
            self.store.set(PENDING_KEY, mid)
            if not self.store.get(SINCE_KEY):
                self.store.set(SINCE_KEY, self.clock())
        self.wake.set()
        self.logger("GITHUB_EVENT_QUEUED", source=mid)
        return True

    async def process_once(self) -> bool:
        """Returns whether a network dispatch was attempted."""
        pending = self.store.get(PENDING_KEY) or 0
        if not pending:
            return False
        now = self.clock()
        earliest = max(
            (self.store.get(SINCE_KEY) or now) + DEBOUNCE_SECONDS,
            (self.store.get(LAST_SENT_KEY) or 0) + MIN_INTERVAL_SECONDS,
            self.store.get(RETRY_AT_KEY) or 0,
        )
        if now < earliest:
            return False
        try:
            await asyncio.to_thread(self.sender, self.token)
        except Exception as exc:
            # Report only a safe reason, not HTTP URLs, errors or credentials.
            label = "GITHUB_REQUEST_FAILED"
            if isinstance(exc, error.HTTPError):
                label = "GITHUB_HTTP_" + str(exc.code)
            elif isinstance(exc, RuntimeError) and str(exc) in {
                "GITHUB_DISPATCH_TOKEN_INVALID", "DISPATCH_HTTP_NOT_ACCEPTED"
            }:
                label = str(exc)
            with self.store.db:
                self.store.set(RETRY_AT_KEY, self.clock() + RETRY_SECONDS)
            self.logger("GITHUB_DISPATCH_FAILED", source=pending, reason=label)
            return True

        # A new event may arrive while the API call is in flight. Never drop it.
        with self.store.db:
            self.store.set(LAST_ACK_ID_KEY, max(
                self.store.get(LAST_ACK_ID_KEY) or 0, pending
            ))
            self.store.set(LAST_SENT_KEY, self.clock())
            self.store.set(RETRY_AT_KEY, 0)
            if (self.store.get(PENDING_KEY) or 0) <= pending:
                self.store.set(PENDING_KEY, 0)
                self.store.set(SINCE_KEY, 0)
            else:
                self.store.set(SINCE_KEY, self.clock())
        self.logger("GITHUB_DISPATCH_ACCEPTED", source=pending)
        return True

    async def run(self, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            await self.process_once()
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
