"""Telegram owner+group delivery and private group discovery regression tests."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

import telegram_delivery as delivery
import telegram_group_setup as setup


class DualDeliveryTests(unittest.TestCase):
    def test_private_only_before_group_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(delivery, "GROUP_STATE", Path(folder) / "missing.json"), \
                    patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "111", "TELEGRAM_GROUP_CHAT_ID": ""}):
                self.assertEqual(delivery.target_chats(), [("personal", "111")])

    def test_private_and_group_use_same_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "group.json"
            path.write_text(json.dumps({"chat_id": -1001234567890, "title": "핫딜 MUSE용"}))
            calls = []
            def fake(req, timeout):
                calls.append(parse_qs(req.data.decode()))
                return io.BytesIO(b'{"ok":true}')
            with patch.object(delivery, "GROUP_STATE", path), \
                    patch.dict(os.environ, {
                        "TELEGRAM_BOT_TOKEN": "dummy-token",
                        "TELEGRAM_CHAT_ID": "111",
                        "TELEGRAM_GROUP_CHAT_ID": "",
                    }), patch.object(delivery.request, "urlopen", side_effect=fake):
                delivery.send_text("🆕 [HOTDEAL]\n\n상품명: 우유\n\n상품링크: https://example.org/123")
            self.assertEqual([c["chat_id"][0] for c in calls], ["111", "-1001234567890"])
            self.assertEqual(calls[0]["text"], calls[1]["text"])
            self.assertIn("https://example.org/123", calls[1]["text"][0])

    def test_group_failure_is_reported_not_marked_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "group.json"
            path.write_text(json.dumps({"chat_id": -1001234567890, "title": "핫딜 MUSE용"}))
            count = []
            def fake(req, timeout):
                count.append(1)
                if len(count) == 2:
                    raise HTTPError("https://example.org/NO_TOKEN", 403, "Forbidden", {}, io.BytesIO(b"{}"))
                return io.BytesIO(b'{"ok":true}')
            with patch.object(delivery, "GROUP_STATE", path), \
                    patch.dict(os.environ, {
                        "TELEGRAM_BOT_TOKEN": "dummy-token",
                        "TELEGRAM_CHAT_ID": "111",
                        "TELEGRAM_GROUP_CHAT_ID": "",
                    }), patch.object(delivery.request, "urlopen", side_effect=fake):
                with self.assertRaisesRegex(RuntimeError, "group delivery rejected"):
                    delivery.send_text("test")
            self.assertEqual(len(count), 2)

    def test_malformed_or_wrong_group_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "group.json"
            path.write_text(json.dumps({"chat_id": -1001234567890, "title": "다른 그룹"}))
            with patch.object(delivery, "GROUP_STATE", path), \
                    patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "111", "TELEGRAM_GROUP_CHAT_ID": ""}):
                with self.assertRaises(RuntimeError):
                    delivery.target_chats()

    def test_discovery_finds_expected_group_only(self):
        updates = [
            {"message": {"chat": {"id": -10055, "title": "딴 그룹", "type": "supergroup"}}},
            {"my_chat_member": {
                "chat": {"id": -1001234567890, "title": "핫딜 MUSE용", "type": "supergroup"},
                "new_chat_member": {"status": "member"},
            }},
        ]
        self.assertEqual(setup.find_group_id(updates), -1001234567890)

    def test_discovery_ignores_private_and_unrelated(self):
        updates = [
            {"message": {"chat": {"id": 12345, "type": "private"}}},
            {"message": {"chat": {"id": -10055, "title": "딴 그룹", "type": "supergroup"}}},
        ]
        self.assertIsNone(setup.find_group_id(updates))


if __name__ == "__main__":
    unittest.main()
