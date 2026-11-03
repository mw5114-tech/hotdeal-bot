"""Offline tests: event-driven Actions dispatch never sends Telegram or posts."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error

from live_store import Store
from jirum_github_dispatch import (
    GitHubEventBridge, coupang_event, workflow_dispatch, ENDPOINT,
    PENDING_KEY, LAST_ACK_ID_KEY, LAST_SENT_KEY, RETRY_AT_KEY, INFLIGHT_KEY,
)

def post(mid, url="https://link.coupang.com/a/example"):
    return {"id": mid, "product_url": url, "links": [url]}


class FakeClock:
    def __init__(self):
        self.now = 10000.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class DispatchHttpTests(unittest.TestCase):
    def test_visible_urls_stop_at_real_whitespace_not_letter_s(self):
        from jirum_github_dispatch import VISIBLE_URL_RE
        url = "https://www.coupang.com/vp/products/123"
        for separator in (" ", "\n", "\t", "\r\n"):
            text = "특가 " + url + separator + "설명 https://example.com/other"
            self.assertEqual(VISIBLE_URL_RE.findall(text), [url, "https://example.com/other"])
            self.assertTrue(coupang_event({"id": 101, "text": text, "entities": []}))
        self.assertFalse(coupang_event({"id": 101, "text": "https://link.coupang.coms.evil/offer", "entities": []}))

    def test_official_workflow_dispatch_endpoint_with_main_and_auth(self):
        seen = {}
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def fake_opener(req, timeout):
            seen.update(url=req.full_url, method=req.get_method(),
                        payload=json.loads(req.data), timeout=timeout,
                        auth=req.get_header("Authorization"))
            return Response()
        workflow_dispatch("test-token", opener=fake_opener)
        self.assertEqual(seen["url"], ENDPOINT)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["payload"], {"ref": "main"})
        self.assertEqual(seen["auth"], "Bearer test-token")
        self.assertEqual(seen["timeout"], 22)

    def test_raw_watcher_plain_text_and_hidden_telegram_links(self):
        # Actual deployed raw_message.extract() shape has no product_url/links.
        raw_plain = {
            "id": 101,
            "text": "🔥 특가 바로가기\\nhttps://link.coupang.com/a/xyz",
            "entities": [],
        }
        raw_hidden = {
            "id": 102,
            "text": "🔥 핫딜 구매 링크",
            "entities": [
                {"type": "text_link", "offset": 6, "length": 4,
                 "url": "https://link.coupang.com/a/hidden"}
            ],
        }
        raw_other = {
            "id": 103,
            "text": "https://toss.im/abc",
            "entities": [{"type": "text_link", "url": "https://evil.example/offer"}],
        }
        self.assertTrue(coupang_event(raw_plain))
        self.assertTrue(coupang_event(raw_hidden))
        self.assertFalse(coupang_event(raw_other))

    def test_bad_raw_entity_and_links_cannot_raise_or_false_trigger(self):
        self.assertFalse(coupang_event({"id": 101, "text": "no links", "entities": [None, 5, {}]}))
        self.assertFalse(coupang_event({"id": 101, "text": "https://link.coupang.com.evil/x", "entities": []}))
        self.assertFalse(coupang_event({"id": 101, "text": "http://link.coupang.com/a", "entities": []}))
        self.assertFalse(coupang_event({"id": 101, "text": "https://link.coupang.com@evil.com/a", "entities": []}))
        self.assertTrue(coupang_event({"id": 101, "text": "https://link.coupang.com/a/xyz).", "entities": []}))

    def test_safe_smoke_input_is_explicit_never_used_by_normal_dispatch(self):
        data = []
        class Resp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def opened(req, timeout):
            data.append(json.loads(req.data))
            return Resp()
        workflow_dispatch("fake-token", opener=opened)
        workflow_dispatch("fake-token", opener=opened, smoke_test=True)
        self.assertEqual(data[0], {"ref": "main"})
        self.assertEqual(data[1], {
            "ref": "main", "inputs": {"smoke_test": "true"}
        })

    def test_only_exact_https_coupang_hosts_trigger(self):
        for url in (
            "https://link.coupang.com/a/deal",
            "https://www.coupang.com/vp/products/123",
            "https://m.coupang.com/vp/products/123",
        ):
            self.assertTrue(coupang_event(post(102, url)))
        for url in (
            "https://evil-coupang.com/a/x",
            "https://link.coupang.com.attacker.com/x",
            "https://example.com/coupang",
            "http://link.coupang.com/a/x",
            "https://example.com/a/x",
        ):
            self.assertFalse(coupang_event(post(102, url)))

    def test_source_id_is_forwarded_without_message_body_or_product_url(self):
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): return False
        def opened(req, timeout):
            self.assertEqual(json.loads(req.data), {
                'ref': 'main', 'inputs': {'source_post_id': '94904'}})
            return Response()
        workflow_dispatch('fake-token', opener=opened, source_post_id=94904)
        for bad in (True, 0, -1, '94904', 2**31):
            with self.assertRaisesRegex(RuntimeError, 'GITHUB_SOURCE_POST_ID_INVALID'):
                workflow_dispatch('fake-token', opener=opened, source_post_id=bad)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "sample.sqlite3")
        self.store.initialize(100, [("personal","123"), ("group","-456")])
        self.clock = FakeClock()
        self.calls = []

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make_bridge(self, sender=None):
        return GitHubEventBridge(
            self.store, "test-token", clock=self.clock,
            sender=sender or self.calls.append,
        )

    async def test_coalesce_two_new_messages_into_one_workflow(self):
        bridge = self.make_bridge()
        self.assertFalse(bridge.observe(post(99)))
        self.assertFalse(bridge.observe(post(101, "https://example.com/hello")))
        self.assertTrue(bridge.observe(post(101)))
        self.assertTrue(bridge.observe(post(102)))
        self.assertFalse(bridge.observe(post(102)))
        self.assertFalse(await bridge.process_once())  # 20-second buffer
        self.clock.advance(21)
        self.assertTrue(await bridge.process_once())
        self.assertEqual(self.calls, ["test-token"])
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 102)
        self.assertEqual(self.store.get(PENDING_KEY), 0)
        self.assertFalse(await bridge.process_once())

    async def test_persist_pending_signal_survives_bridge_restart(self):
        first = self.make_bridge()
        first.observe(post(101))
        second = self.make_bridge()
        self.clock.advance(25)
        self.assertTrue(await second.process_once())
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 101)
        self.assertFalse(second.observe(post(101)))

    async def test_retry_later_without_dropping_pending_on_http_failure(self):
        attempts = []
        def fail(_token):
            attempts.append("failed")
            raise error.HTTPError("dummy", 403, "blocked", {}, None)
        bridge = self.make_bridge(sender=fail)
        bridge.observe(post(101))
        self.clock.advance(21)
        self.assertTrue(await bridge.process_once())
        self.assertEqual(attempts, ["failed"])
        self.assertEqual(self.store.get(PENDING_KEY), 101)
        self.assertIsNone(self.store.get(LAST_ACK_ID_KEY))
        self.assertFalse(await bridge.process_once())
        self.clock.advance(180)
        self.assertTrue(await bridge.process_once())
        self.assertEqual(len(attempts), 2)

    async def test_next_message_survives_while_request_is_in_flight(self):
        started = threading.Event()
        release = threading.Event()
        def sender(_token):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test sender timed out")
            self.calls.append(_token)
        bridge = self.make_bridge(sender=sender)
        bridge.observe(post(101))
        self.clock.advance(25)
        task = asyncio.create_task(bridge.process_once())
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        self.assertTrue(bridge.observe(post(102)))
        release.set()
        await task
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 101)
        self.assertEqual(self.store.get(PENDING_KEY), 102)
        self.clock.advance(80)
        self.assertTrue(await bridge.process_once())
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 102)
        self.assertEqual(len(self.calls), 2)

    async def test_lost_response_blocks_retry_even_after_database_reopen(self):
        attempts = []
        def lost_response(token):
            attempts.append(token)
            raise TimeoutError('response lost after acceptance')
        bridge = self.make_bridge(sender=lost_response)
        bridge.observe(post(101))
        self.clock.advance(21)
        self.assertTrue(await bridge.process_once())
        self.assertEqual(self.store.get(INFLIGHT_KEY), 101)
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'sample.sqlite3')
        bridge = self.make_bridge(sender=lost_response)
        bridge.observe(post(102))
        self.clock.advance(10000)
        self.assertFalse(await bridge.process_once())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.store.get(PENDING_KEY), 102)

    async def test_crash_after_intent_commit_never_resends(self):
        bridge = self.make_bridge()
        bridge.observe(post(101))
        with self.store.db:
            self.store.set(INFLIGHT_KEY, 101)
        self.clock.advance(10000)
        self.assertFalse(await self.make_bridge().process_once())
        self.assertEqual(self.calls, [])

    async def test_default_sender_passes_coalesced_id_to_github(self):
        bridge = GitHubEventBridge(self.store, 'test-token', clock=self.clock)
        bridge.observe(post(101))
        bridge.observe(post(102))
        self.clock.advance(21)
        with patch('jirum_github_dispatch.workflow_dispatch') as dispatch:
            self.assertTrue(await bridge.process_once())
        dispatch.assert_called_once_with('test-token', source_post_id=102)
        self.assertEqual(self.store.get(LAST_ACK_ID_KEY), 102)


if __name__ == "__main__":
    unittest.main()
