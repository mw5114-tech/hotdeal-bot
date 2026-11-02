"""Offline integration checks for Muse official receipt -> existing FIFO pipeline."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.daangn_editor_probe import read_one_draft
from gh_only.jirum_manual_queue import ingest, scan
from gh_only.test_daangn_editor_probe import DaangnEditorOfflineTests
from gh_only.test_jirum_manual_queue import FakeStore, baseline, deal


OFFICIAL = "https://link.coupang.com/a/il321hccMe"


class OfficialIntegrationTests(unittest.TestCase):
    def test_queue_without_receipt_blocks_browser_and_post(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        store = FakeStore(state)
        with patch.dict(os.environ, {"JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "true"}):
            with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
                with patch("gh_only.jirum_manual_queue.load_receipt", return_value=None):
                    result = scan(store)
        self.assertFalse(result["has_deal"])
        self.assertFalse(result["official_link_ready"])
        self.assertEqual([row["id"] for row in store.value["pending"]], [94744])
        self.assertEqual(store.value["published"].get("94744"), None)

    def test_ready_receipt_enables_queue(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        store = FakeStore(state)
        with patch.dict(os.environ, {"JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "true"}):
            with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
                with patch("gh_only.jirum_manual_queue.load_receipt", return_value={"partners_link": OFFICIAL}):
                    result = scan(store)
        self.assertTrue(result["has_deal"])
        self.assertTrue(result["official_link_ready"])

    def test_official_url_in_editor_and_disclosure(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            row = DaangnEditorOfflineTests()._make_draft(folder)["drafts"][0]
            old = row["composed_affiliate_url"]
            row["caption"]["caption"] = row["caption"]["caption"].replace(old, OFFICIAL)
            row["composed_affiliate_url"] = OFFICIAL
            row["official_link_receipt"] = {
                "telegram_message_id": row["source_message_id"],
                "partners_link": OFFICIAL,
            }
            row["affsdp"] = {
                "status": "OFFICIAL_PARTNERS_LINK_RECEIPT",
                "official_link_issued": True,
                "commission_attribution_verified": False,
            }
            (folder / "drafts.json").write_text(
                json.dumps({"drafts": [row]}, ensure_ascii=False), encoding="utf-8"
            )
            article = read_one_draft(folder)
            self.assertEqual(article["candidate_url"], OFFICIAL)
            self.assertFalse(article["unissued_affsdp_candidate"])
            self.assertEqual(article["body"].count(OFFICIAL), 1)
            self.assertIn("파트너스", article["body"])


if __name__ == "__main__":
    unittest.main()
