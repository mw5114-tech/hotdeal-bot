"""Unit checks for public channel IDs, exact forwarding, baseline and retries."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jirum_forward as app


HTML = '''<html><div class="tgme_widget_message" data-post="jirum/100">A</div>
<div class="tgme_widget_message" data-post="jirum/102">B</div>
<div data-post="some_other_channel/104">C</div>
<div data-post="jirum/101">D</div></html>'''


class Tests(unittest.TestCase):
    def test_parse_listing(self):
        self.assertEqual(app.parse_listing(HTML), [100, 101, 102])

    def test_parse_empty_refuses_to_reset(self):
        with self.assertRaises(RuntimeError):
            app.parse_listing("<html>no posts</html>")

    def test_telegram_forward_uses_original_channel_id_not_rewritten_text(self):
        import io
        import os
        from urllib.parse import parse_qs
        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "dummy-test-token",
            "TELEGRAM_CHAT_ID": "12345678",
        }):
            with patch.object(
                app.request, "urlopen", return_value=io.BytesIO(b'{"ok": true}')
            ) as urlopen:
                app.forward_message(87548)
        request_obj = urlopen.call_args.args[0]
        self.assertTrue(request_obj.full_url.endswith("/forwardMessage"))
        self.assertEqual(
            parse_qs(request_obj.data.decode("utf-8")),
            {"chat_id": ["12345678"], "from_chat_id": ["@jirum"], "message_id": ["87548"]},
        )

    def test_initial_baseline_forwards_only_latest_once(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                forwarded = []
                app.run(fetcher=lambda: [100, 101], forwarder=forwarded.append)
                self.assertEqual(forwarded, [101])
                self.assertEqual(app.load()["last_seen_id"], 101)

    def test_new_messages_only_and_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                app.save({"last_seen_id": 100})
                forwarded = []
                for _ in range(2):
                    app.run(fetcher=lambda: [103, 101, 102, 100], forwarder=forwarded.append)
                self.assertEqual(forwarded, [101, 102, 103])
                self.assertEqual(app.load()["last_seen_id"], 103)

    def test_partial_forward_failure_retries_next_time(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                app.save({"last_seen_id": 100})
                calls = []
                def forward(id):
                    calls.append(id)
                    if id == 102:
                        raise RuntimeError("Telegram unavailable")
                with self.assertRaises(RuntimeError):
                    app.run(fetcher=lambda: [100, 101, 102, 103], forwarder=forward)
                self.assertEqual(app.load()["last_seen_id"], 101)
                forwarded = []
                app.run(fetcher=lambda: [100, 101, 102, 103], forwarder=forwarded.append)
                self.assertEqual(forwarded, [102, 103])

    def test_preview_does_not_forward_or_write(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                app.save({"last_seen_id": 100})
                app.run("preview", lambda: [101, 102], lambda _: self.fail("forwarded in preview"))
                self.assertEqual(app.load()["last_seen_id"], 100)

    def test_manual_verify_forwards_latest_without_changing_state(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                app.save({"last_seen_id": 100})
                forwarded = []
                app.run("verify-forward", lambda: [101, 102], forwarded.append)
                self.assertEqual(forwarded, [102])
                self.assertEqual(app.load()["last_seen_id"], 100)

    def test_invalid_state_blocks_baseline_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(app, "STATE", Path(folder) / "jirum.json"):
                app.STATE.write_text(json.dumps({"last_seen_id": "oops"}))
                with self.assertRaises(RuntimeError):
                    app.run(fetcher=lambda: [123], forwarder=lambda _: self.fail("invalid state"))


if __name__ == "__main__":
    unittest.main()
