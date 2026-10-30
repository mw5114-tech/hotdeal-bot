import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib import error
from telethon.tl.custom.message import Message as RealMessage
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl, PeerChannel
import raw_runtime as app
from live_store import Store
from raw_delivery import Bot, DeliveryError
TARGETS = [('personal', '123'), ('group', '-456')]

def Message(**kwargs):
    return RealMessage(peer_id=PeerChannel(42), **kwargs)

class StoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.store = Store(self.path)
        self.store.initialize(100, TARGETS)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def put(self, ident=101, parts=None, body='body', **kwargs):
        self.store.put(ident, body, ['payload'] if parts is None else parts, TARGETS, **kwargs)

    def test_baseline_no_historical_delivery(self):
        self.put(99)
        self.put(100)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get('cursor'), 100)

    def test_targets_survive_json_roundtrip_and_reject_changes(self):
        self.store.initialize(200, TARGETS)
        self.assertEqual(self.store.get('baseline'), 100)
        with self.assertRaisesRegex(RuntimeError, 'DESTINATIONS_CHANGED'):
            self.store.initialize(100, [('personal', '999'), TARGETS[1]])

    def test_partial_success_retry_only_failed_target(self):
        self.put()
        first = self.store.claim()
        self.store.finish(first, 'sent', remote_id=1)
        second = self.store.claim()
        self.assertNotEqual(first['destination'], second['destination'])
        self.store.finish(second, 'retry', reason='RATE_LIMITED', delay=10)
        self.assertIsNone(self.store.claim(now=0))
        retried = self.store.claim(now=10 ** 12)
        self.assertEqual(retried['destination'], second['destination'])
        self.store.finish(retried, 'sent', remote_id=2)
        self.put()
        self.assertIsNone(self.store.claim())

    def test_restart_keeps_sent_and_recovers_uncertain(self):
        self.put()
        first = self.store.claim()
        self.store.finish(first, 'sent', remote_id=17)
        other = self.store.claim()
        self.store.close()
        self.store = Store(self.path)
        self.store.recover()
        self.assertIsNone(self.store.claim())
        self.assertEqual(len(self.store.summary()['review']), 1)
        self.store.resolve('live', 101, other['destination'], 0, 'retry')
        self.assertEqual(self.store.claim()['destination'], other['destination'])

    def test_edit_updates_unsent_but_freezes_partial_payload(self):
        self.put(body='v1')
        self.put(body='v2', parts=['new'])
        first = self.store.claim()
        self.assertEqual(first['text'], 'new')
        self.store.finish(first, 'sent', remote_id=1)
        self.put(body='v3', parts=['must not differ'])
        second = self.store.claim()
        self.assertEqual(second['text'], 'new')
        self.store.finish(second, 'sent', remote_id=2)
        self.put(body='v4', parts=['do not resend'])
        self.assertIsNone(self.store.claim())

    def test_missing_link_can_be_added_by_edit(self):
        self.put(parts=[])
        self.assertIsNone(self.store.claim())
        self.put(parts=['added purchase URL'])
        self.assertIsNotNone(self.store.claim())

    def test_multipart_cannot_overtake_failed_part(self):
        self.put(parts=['part1', 'part2'])
        row = self.store.claim()
        self.store.finish(row, 'uncertain', reason='TIMEOUT')
        other = self.store.claim()
        self.assertEqual(other['part'], 0)
        self.assertNotEqual(other['destination'], row['destination'])
        self.store.finish(other, 'sent', remote_id=1)
        next_row = self.store.claim()
        self.assertEqual(next_row['part'], 1)
        self.assertEqual(next_row['destination'], other['destination'])

    def test_out_of_order_events_do_not_advance_scan_cursor(self):
        self.put(105)
        self.assertEqual(self.store.get('cursor'), 100)
        self.put(101, scanned=True)
        self.assertEqual(self.store.get('cursor'), 101)

    def test_manual_test_separate_from_production(self):
        self.put(95, scope='test:unique')
        self.assertIsNone(self.store.claim())
        self.assertIsNotNone(self.store.claim('test:unique'))
        self.assertEqual(self.store.get('cursor'), 100)

class AsyncTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.store.initialize(100, TARGETS)
        self.store.db.execute('PRAGMA synchronous=OFF')

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_partial_send_then_retry_never_repeats_success(self):
        self.store.put(101, 'body', ['identical'], TARGETS)
        calls = []

        class FakeBot:

            def send(self, chat, text):
                calls.append((chat, text))
                if chat == '123' and sum((c[0] == '123' for c in calls)) == 1:
                    raise DeliveryError('retry', 'RATE_LIMITED', 0)
                return 1
        with redirect_stdout(io.StringIO()):
            while await app.deliver_one(self.store, FakeBot()):
                pass
        self.assertEqual(sum((c[0] == '-456' for c in calls)), 1)
        self.assertEqual(sum((c[0] == '123' for c in calls)), 2)
        self.assertTrue(all((c[1] == 'identical' for c in calls)))

    async def test_paginated_recovery_and_event_deduplication(self):

        class Client:

            def iter_messages(self, channel, min_id, **kwargs):

                async def rows():
                    for ident in range(min_id + 1, min(min_id + 100, 305) + 1):
                        yield Message(id=ident, message=f'상품 {ident}\nhttps://example.com/{ident}')
                return rows()
        wake = asyncio.Event()
        await app.reconcile(Client(), None, self.store, TARGETS, wake)
        self.assertEqual(self.store.get('cursor'), 305)
        count = self.store.db.execute('SELECT count(*) FROM delivery').fetchone()[0]
        self.assertEqual(count, 205 * 2)
        await app.reconcile(Client(), None, self.store, TARGETS, wake)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM delivery').fetchone()[0], count)

    async def test_shadow_registers_only_source_handlers_and_never_sends(self):
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'shadow.sqlite3')
        handlers = []
        loop = asyncio.get_running_loop()

        class Client:
            disconnected = loop.create_future()

            async def get_messages(self, *args, **kwargs):
                return [Message(id=110, message='old https://example.com/old')]

            def add_event_handler(self, callback, event):
                handlers.append((callback, event))

            async def catch_up(self):
                msg = Message(id=111, message='new https://example.com/new')
                await handlers[0][0](SimpleNamespace(message=msg))
                loop.call_later(0.02, self.disconnected.set_result, None)

            def iter_messages(self, *args, **kwargs):

                async def empty():
                    if False:
                        yield None
                return empty()

            async def disconnect(self):
                pass

        async def connected(_):
            return (Client(), SimpleNamespace(id=42))
        with patch.object(app, 'connected', connected), patch.object(app, 'targets', return_value=TARGETS), patch.object(app, 'Bot', side_effect=AssertionError('shadow must not send')), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'WORKER_OR_CONNECTION_STOPPED'):
                await app.run_service(self.store, False)
        self.assertEqual([type(e).__name__ for _, e in handlers], ['NewMessage', 'MessageEdited'])
        self.assertEqual(self.store.get('baseline'), 110)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM delivery WHERE attempts>0').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM posts').fetchone()[0], 1)

    async def test_interrupted_reconciliation_resumes_after_durable_cursor(self):

        class Client:

            def iter_messages(self, channel, min_id, **kwargs):

                async def messages():
                    if min_id == 100:
                        yield Message(id=101, message='first https://example.com/1')
                        raise ConnectionError('simulated interruption')
                    if min_id == 101:
                        yield Message(id=102, message='next https://example.com/2')
                return messages()
        with self.assertRaises(ConnectionError):
            await app.reconcile(Client(), None, self.store, TARGETS, asyncio.Event())
        self.assertEqual(self.store.get('cursor'), 101)
        await app.reconcile(Client(), None, self.store, TARGETS, asyncio.Event())
        self.assertEqual(self.store.get('cursor'), 102)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM delivery').fetchone()[0], 4)

class CommandTests(unittest.TestCase):

    def invoke(self, folder, *args):
        with patch.dict(os.environ, {'JIRUM_DATA_DIR': folder, 'JIRUM_LIVE_ENABLED': 'false'}), patch.object(sys, 'argv', ['jirum_live.py', *args]), patch.object(app, 'targets', return_value=TARGETS), redirect_stdout(io.StringIO()):
            return app.main()

    def test_cutover_requires_successful_two_target_test(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.invoke(folder, 'arm', '--cutover-id', '150'), 1)
            live = Store(Path(folder) / 'jirum-live.sqlite3')
            self.assertIsNone(live.get('baseline'))
            live.close()

    def test_cutover_handoff_preserves_exact_checkpoint(self):
        import time
        with tempfile.TemporaryDirectory() as folder:
            test = Store(Path(folder) / 'jirum-test.sqlite3')
            test.put(140, 'test body', ['test'], TARGETS, scope='test:one')
            for _ in range(2):
                row = test.claim('test:one')
                test.finish(row, 'sent', remote_id=1)
            with test.db:
                test.set('verification', {'at': time.time(), 'scope': 'test:one', 'targets': TARGETS})
            test.close()
            self.assertEqual(self.invoke(folder, 'arm', '--cutover-id', '150'), 0)
            live = Store(Path(folder) / 'jirum-live.sqlite3')
            self.assertEqual(live.get('baseline'), 150)
            self.assertEqual(live.get('cursor'), 150)
            self.assertIsNone(live.claim())
            live.close()
            self.assertEqual(self.invoke(folder, 'arm', '--cutover-id', '160'), 1)

    def test_one_process_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'watcher.lock'
            with app.exclusive(path):
                with self.assertRaisesRegex(RuntimeError, 'ANOTHER_PROCESS_RUNNING'):
                    with app.exclusive(path):
                        self.fail('duplicate process allowed')
