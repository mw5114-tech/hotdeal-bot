"""Offline regression: restore old /re/AFFSDP publication, no Muse link edits."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.affsdp_candidate import compose_affsdp_candidate
from gh_only.jirum_manual_queue import build_next, finish, ingest, scan
from gh_only.test_daangn_editor_probe import DaangnEditorOfflineTests
from gh_only.test_jirum_manual_queue import FakeStore, baseline, deal
from gh_only.daangn_editor_probe import read_one_draft

OWNER = "AF9668915"
PRODUCT = "21569963"
ITEM = "84202538"
EXPECTED = "https://link.coupang.com/re/AFFSDP?lptag=AF9668915&pageKey=21569963&itemId=84202538"
IDS = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": PRODUCT, "item_id": ITEM}


class RestoreOriginalAffsdpTests(unittest.TestCase):
    def test_same_old_pattern_exactly(self):
        result = compose_affsdp_candidate(IDS, OWNER)
        self.assertEqual(result["candidate_url"], EXPECTED)
        self.assertEqual(result["status"], "UNISSUED_AFFSDP_CANDIDATE")
        self.assertFalse(result["official_link_issued"])
        self.assertFalse(result["commission_attribution_verified"])

    def test_no_muse_receipt_required_for_new_telegram_deal(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        store = FakeStore(state)
        with patch.dict(os.environ, {
            "JIRUM_PUBLISH_BEFORE_MUSE_REWRITE": "false",
            "JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "false",
            "JIRUM_REQUIRE_FRESH_POST": "false",
        }), patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertTrue(result["has_deal"])
        self.assertEqual(result["source_id"], 94744)

    def test_build_next_keeps_affsdp_with_owner_lptag(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        candidate = {
            "source_message_id": 94744, "product_identity": IDS,
            "composed_affiliate_url": EXPECTED,
            "caption": {"caption": "상품명\n\n🛒 쿠팡에서 상품정보 더보기 : " + EXPECTED},
            "affsdp": {"status": "UNISSUED_AFFSDP_CANDIDATE",
                       "official_link_issued": False,
                       "commission_attribution_verified": False},
        }
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {
                "JIRUM_PUBLISH_BEFORE_MUSE_REWRITE": "false",
                "JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "false",
                "COUPANG_PARTNER_LPTAG": OWNER,
            }), patch("gh_only.jirum_manual_queue.DRAFT_DIR", Path(d)), \
                patch("gh_only.default_deal_automation.enrich_deal", return_value=candidate), \
                patch("gh_only.jirum_manual_queue.validate_draft", return_value={
                    "source_id": 94744, "candidate_url": EXPECTED,
                    "product_id": PRODUCT, "item_id": ITEM,
                    "muse_rewrite_pending": False,
                }):
                article = build_next(state)
            recorded = json.loads((Path(d) / "drafts.json").read_text(encoding="utf-8"))["drafts"][0]
        self.assertEqual(article["candidate_url"], EXPECTED)
        self.assertEqual(recorded["composed_affiliate_url"], EXPECTED)
        self.assertEqual(recorded["caption"]["caption"].count(EXPECTED), 1)
        self.assertFalse(recorded.get("muse_rewrite_pending", False))

    def test_legacy_draft_keeps_intact_url_with_compact_spacing(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            report = DaangnEditorOfflineTests()._make_draft(folder)
            before = report["drafts"][0]["composed_affiliate_url"]
            report["drafts"][0]["composed_affiliate_url"] = EXPECTED
            report["drafts"][0]["caption"]["caption"] = report["drafts"][0]["caption"]["caption"].replace(before, EXPECTED)
            (folder / "drafts.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            article = read_one_draft(folder)
        self.assertEqual(article["candidate_url"], EXPECTED)
        self.assertTrue(article["unissued_affsdp_candidate"])
        self.assertFalse(article["muse_rewrite_pending"])
        self.assertIn("🛒 쿠팡에서 상품정보 더보기 : " + EXPECTED, article["body"])
        self.assertEqual(article["body"].count(EXPECTED), 1)

    def test_published_does_not_wait_for_muse(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        state["in_flight"] = {"id": 94744, "status": "CLAIMED",
                              "run_id": "testrestore", "at": "now"}
        store = FakeStore(state)
        finish(store, 94744, "testrestore", {
            "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
            "post_url": "https://cafe.daangn.com/i-gagyeog-mos-c/posts/test"
        })
        self.assertEqual(store.value["published"]["94744"]["status"], "PUBLISHED_VERIFIED")
        self.assertEqual(store.value["pending"], [])
        self.assertIsNone(store.value["in_flight"])


if __name__ == "__main__":
    unittest.main()
