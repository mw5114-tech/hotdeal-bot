"""Offline contract tests for GitHub-first post, then Muse Daangn edit."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.daangn_editor_probe import read_one_draft
from gh_only.direct_product_links import regular_product_url
from gh_only.jirum_manual_queue import build_next, finish, freshness_status, scan, ingest
from gh_only.test_jirum_manual_queue import FakeStore, baseline, deal
from gh_only.test_daangn_editor_probe import DaangnEditorOfflineTests

IDS = {"status": "EXACT_ITEM_IDENTIFIED",
       "product_id": "9440621860", "item_id": "28512619980"}
DIRECT = "https://www.coupang.com/vp/products/9440621860?itemId=28512619980"
CANDIDATE = ("https://link.coupang.com/re/AFFSDP?"
             "lptag=AF9668915&pageKey=9440621860&itemId=28512619980")


class MusePostEditOfflineTests(unittest.TestCase):
    def test_daangn_draft_accepts_exact_regular_url_pending_muse(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report = DaangnEditorOfflineTests()._make_draft(folder)
            row = report["drafts"][0]
            old = row["composed_affiliate_url"]
            row["caption"]["caption"] = row["caption"]["caption"].replace(old, DIRECT)
            row.update({"composed_affiliate_url": DIRECT,
                        "muse_rewrite_pending": True,
                        "product_identity": IDS,
                        "affsdp": {"status": "REGULAR_PRODUCT_AWAITING_MUSE",
                                   "official_link_issued": False,
                                   "commission_attribution_verified": False}})
            (folder / "drafts.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            article = read_one_draft(folder)
            self.assertTrue(article["muse_rewrite_pending"])
            self.assertFalse(article["unissued_affsdp_candidate"])
            self.assertEqual(article["candidate_url"], DIRECT)
            self.assertEqual(article["body"].count(DIRECT), 1)
            self.assertTrue(article["body"].endswith("수수료를 제공받습니다."))

    def test_muse_mode_builds_direct_product_and_not_affsdp(self):
        state = baseline()
        state["last_scanned_id"] = 94744
        state["pending"] = [{
            "id": 94744,
            "source_post_url": "https://t.me/jirum/94744",
            "original_purchase_url": "https://link.coupang.com/a/originalSrc",
            "source_text": "상품 특가 12,050원",
            "posted_at_utc": "2026-11-03T01:10:00Z",
            "title": "Example",
            "sale_price_krw": 12050,
        }]
        candidate = {
            "source_message_id": 94744,
            "product_identity": IDS,
            "composed_affiliate_url": CANDIDATE,
            "caption": {"caption": "상품명\n\n🛒 쿠팡에서 상품정보 더보기 : " + CANDIDATE},
            "affsdp": {"status": "UNISSUED_AFFSDP_CANDIDATE"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {
                "JIRUM_PUBLISH_BEFORE_MUSE_REWRITE": "true",
                "JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "false",
            }), patch("gh_only.jirum_manual_queue.DRAFT_DIR", Path(tmp)), \
               patch("gh_only.default_deal_automation.enrich_deal", return_value=candidate), \
               patch("gh_only.jirum_manual_queue.validate_draft", return_value={
                   "source_id": 94744,
                   "candidate_url": DIRECT,
                   "muse_rewrite_pending": True,
                   "product_id": IDS["product_id"],
                   "item_id": IDS["item_id"],
               }):
                article = build_next(state)
            report = json.loads((Path(tmp) / "drafts.json").read_text("utf-8"))
        row = report["drafts"][0]
        self.assertEqual(article["candidate_url"], DIRECT)
        self.assertEqual(row["caption"]["caption"].count(DIRECT), 1)
        self.assertNotIn(CANDIDATE, row["caption"]["caption"])
        self.assertEqual(row["affsdp"]["status"], "REGULAR_PRODUCT_AWAITING_MUSE")
        self.assertFalse(row["affsdp"]["official_link_issued"])

    def test_published_state_has_muse_rewrite_target_and_source_age(self):
        posted = "2026-11-03T01:10:00Z"
        state = baseline()
        state["last_scanned_id"] = 94744
        state["pending"] = [{
            "id": 94744, "source_post_url": "https://t.me/jirum/94744",
            "original_purchase_url": DIRECT, "posted_at_utc": posted,
            "title": "Demo",
        }]
        state["in_flight"] = {"id": 94744, "status": "CLAIMED",
                              "run_id": "run-test", "at": "now"}
        store = FakeStore(state)
        finish(store, 94744, "run-test", {
            "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
            "post_url": "https://cafe.daangn.com/i-gagyeog-mos-c/posts/test",
            "muse_rewrite_pending": True,
            "product_id": IDS["product_id"], "item_id": IDS["item_id"],
        })
        published = store.value["published"]["94744"]
        self.assertEqual(published["status"], "PUBLISHED_AWAITING_MUSE")
        self.assertEqual(published["posted_at_utc"], posted)
        self.assertEqual(published["product_id"], IDS["product_id"])
        self.assertFalse(store.value["pending"])
        self.assertIsNone(store.value["in_flight"])

    def test_no_official_receipt_required_for_initial_post(self):
        state, _, _ = ingest(baseline(), [deal(94744)])
        store = FakeStore(state)
        with patch.dict(os.environ, {
            "JIRUM_PUBLISH_BEFORE_MUSE_REWRITE": "true",
            "JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "false",
        }), patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertTrue(result["has_deal"])
        self.assertEqual(result["source_id"], 94744)

    def test_exact_60_minutes_is_expired(self):
        now = datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)
        row = {"posted_at_utc": (now - timedelta(minutes=60)).isoformat()}
        self.assertEqual(freshness_status(row, reference=now),
                         "DEAL_OVER_60_MINUTES_OLD")


if __name__ == "__main__":
    unittest.main()
