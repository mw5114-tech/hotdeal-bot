"""Offline end-to-end checks for actual RAW_MODE -> GitHub event bridge.

No live Telegram/GitHub credentials, network requests, or cafe submissions.
"""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telethon.tl.custom.message import Message
from telethon.tl.types import PeerChannel, MessageEntityTextUrl

import raw_runtime as raw
from raw_message import extract, format_parts
from jirum_github_dispatch import (
    GitHubEventBridge, PENDING_KEY, LAST_ACK_ID_KEY,
)
from live_store import Store

TARGETS = [("personal", "123"), ("group", "-456")]


def message(mid, text, entities=None):
    return Message(id=mid, peer_id=PeerChannel(42), message=text, entities=entities)


class Clock:
    def __init__(self):
        self.t = 17000.0
    def __call__(self):
        return self.t


class RawBridgeIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "raw-live.sqlite3")
        self.store.initialize(100, TARGETS)

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_reconcile_saves_original_telegram_content_and_dispatches_once(self):
        clock = Clock()
        requests = []
        bridge = GitHubEventBridge(
            self.store, "fake-server-token", clock=clock, sender=requests.append
        )
        exact = "  🔥 판매가 9,900원\\nhttps://link.coupang.com/a/deal?x=1&not=2\\n"
        other = "다른 사이트 https://example.com/not-coupang"
        rows = [message(101, other), message(102, exact),
                message(103, "쿠팡 숨은 링크", [
                    MessageEntityTextUrl(0, 2, "https://link.coupang.com/a/hide")
                ])]
        class FakeClient:
            def iter_messages(self, channel, min_id, **kwargs):
                async def iterable():
                    for row in rows:
                        if row.id > min_id:
                            yield row
                return iterable()
        with redirect_stdout(io.StringIO()):
            await raw.reconcile(
                FakeClient(), None, self.store, TARGETS, asyncio.Event(),
                github_bridge=bridge
            )
        self.assertEqual(self.store.get("cursor"), 103)
        self.assertEqual(self.store.get(PENDING_KEY), 103)
        saved = self.store.db.execute(
            "SELECT body FROM posts WHERE scope='live' AND id=102"
        ).fetchone()[0]
        self.assertEqual(saved, exact)
        sent_payload = self.store.db.execute(
            "SELECT text FROM delivery WHERE post_id=102 LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(json.loads(sent_payload)["text"], exact)
        clock.t += 25
        self.assertTrue(await bridge.process_once())
        self.assertEqual(requests, ["fake-server-token"])
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 103)
        with redirect_stdout(io.StringIO()):
            await raw.reconcile(
                FakeClient(), None, self.store, TARGETS, asyncio.Event(),
                github_bridge=bridge
            )
        self.assertEqual(self.store.get(PENDING_KEY), 0)
        self.assertFalse(await bridge.process_once())

    async def test_real_raw_new_message_handler_queues_when_opted_in(self):
        handlers = []
        loop = asyncio.get_running_loop()
        class Client:
            disconnected = loop.create_future()
            async def get_messages(self, *a, **k):
                return []
            def add_event_handler(self, callback, event):
                handlers.append(callback)
            async def catch_up(self):
                await handlers[0](SimpleNamespace(message=message(
                    101, "🍀 쿠팡: https://link.coupang.com/a/abc"
                )))
                loop.call_later(.04, self.disconnected.set_result, None)
            def iter_messages(self, *a, **k):
                async def empty():
                    if False: yield None
                return empty()
            async def disconnect(self):
                pass
        class Bot:
            def check_targets(self, configured):
                return True
            def send(self, chat, payload):
                return 1
        async def fake_connected(configured):
            return Client(), SimpleNamespace(id=42)
        env = {
            "JIRUM_GITHUB_DISPATCH_ENABLED": "true",
            "JIRUM_GITHUB_DISPATCH_TOKEN": "fake-no-network",
        }
        with patch.dict(os.environ, env), \
                patch.object(raw, "connected", fake_connected), \
                patch.object(raw, "targets", return_value=TARGETS), \
                patch.object(raw, "Bot", Bot), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(
                RuntimeError, "WORKER_OR_CONNECTION_STOPPED"
            ):
                await raw.run_service(self.store, enabled=True)
        self.assertEqual(self.store.get(PENDING_KEY), 101)
        self.assertEqual(len(handlers), 2)

    async def test_shadow_never_dispatches_even_if_flag_enabled(self):
        self.store.close()
        self.store = Store(Path(self.tmp.name) / "shadow.sqlite3")
        handlers = []
        loop = asyncio.get_running_loop()
        class Client:
            disconnected = loop.create_future()
            async def get_messages(self, *a, **k):
                return [message(100, "baseline")]
            def add_event_handler(self, callback, event):
                handlers.append(callback)
            async def catch_up(self):
                await handlers[0](SimpleNamespace(
                    message=message(101, "https://link.coupang.com/a/x")
                ))
                loop.call_later(.04, self.disconnected.set_result, None)
            def iter_messages(self, *a, **k):
                async def empty():
                    if False: yield None
                return empty()
            async def disconnect(self):
                pass
        async def fake_connected(configured):
            return Client(), SimpleNamespace(id=42)
        with patch.dict(os.environ, {
                "JIRUM_GITHUB_DISPATCH_ENABLED": "true",
                "JIRUM_GITHUB_DISPATCH_TOKEN": "fake",
            }), patch.object(raw, "connected", fake_connected), \
                patch.object(raw, "targets", return_value=TARGETS), \
                patch.object(raw, "Bot", side_effect=AssertionError(
                    "shadow must never send Telegram messages"
                )), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "WORKER_OR_CONNECTION_STOPPED"):
                await raw.run_service(self.store, enabled=False)
        self.assertIsNone(self.store.get(PENDING_KEY))

    async def test_bridge_failure_does_not_break_raw_delivery_path(self):
        class BrokenBridge:
            def observe(self, post):
                raise RuntimeError("broken optional integration")
        post = extract(message(101, "상품   내용\n"))
        with redirect_stdout(io.StringIO()):
            raw.signal_github(BrokenBridge(), post)
        self.store.put(post["id"], post["text"], format_parts(post), TARGETS)
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM delivery WHERE post_id=101"
            ).fetchone()[0], 2
        )


if __name__ == "__main__":
    unittest.main()
