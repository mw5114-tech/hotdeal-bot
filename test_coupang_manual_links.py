"""Tests: no affiliate substitution unless source and manually generated URL are exact."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import coupang_manual_links as affiliate
import jirum_forward as jirum


OLD = "https://link.coupang.com/a/originalSource"
OWN = "https://link.coupang.com/a/ownPersonalLink"
OTHER = "https://link.coupang.com/a/anotherProduct"
TOSS = "https://toss.im/_m/test"


class CoupangManualLinksTests(unittest.TestCase):
    def test_without_manual_map_existing_links_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(affiliate, "CONFIG", Path(directory) / "missing.json"):
                self.assertEqual(affiliate.resolve_manual_link(OLD), OLD)
                self.assertEqual(affiliate.resolve_manual_link(TOSS), TOSS)

    def test_only_exact_source_is_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "map.json"
            config.write_text(json.dumps({"mappings": {OLD: OWN}}), encoding="utf-8")
            with patch.object(affiliate, "CONFIG", config):
                self.assertEqual(affiliate.resolve_manual_link(OLD), OWN)
                self.assertEqual(affiliate.resolve_manual_link(OTHER), OTHER)
                self.assertEqual(affiliate.resolve_manual_link(TOSS), TOSS)
                self.assertEqual(affiliate.resolve_manual_link(OLD + "?different=true"), OLD + "?different=true")

    def test_bad_replacement_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "map.json"
            config.write_text(json.dumps({"mappings": {OLD: "https://evil.example.com/a/own"}}), encoding="utf-8")
            with patch.object(affiliate, "CONFIG", config):
                with self.assertRaises(RuntimeError):
                    affiliate.resolve_manual_link(OLD)

    def test_invalid_mapping_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "map.json"
            config.write_text("not json", encoding="utf-8")
            with patch.object(affiliate, "CONFIG", config):
                with self.assertRaises(RuntimeError):
                    affiliate.resolve_manual_link(OLD)

    def test_message_contains_personal_link_but_no_old_link(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "map.json"
            config.write_text(json.dumps({"mappings": {OLD: OWN}}), encoding="utf-8")
            post = {
                "id": 987654,
                "title": "쿠팡 상품",
                "product_url": OLD,
                "text": f"쿠팡 상품\n📦 특가 바로가기\n{OLD}\n이 포스팅은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.",
            }
            with patch.object(affiliate, "CONFIG", config):
                message = jirum.make_message(post)
            self.assertIn("특가 바로가기\n" + OWN, message)
            self.assertNotIn(OLD, message)
            self.assertIn("수수료를 제공받습니다", message)


if __name__ == "__main__":
    unittest.main()
