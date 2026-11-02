"""Offline: one-shot replay leaves production FIFO history untouched."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from gh_only.one_shot_test_repost import select_latest_published, prepare_test_article
from gh_only.test_jirum_manual_queue import baseline, deal


def state():
    s = baseline()
    s["last_scanned_id"] = 94884
    s["published"]["94884"] = {
        "status": "PUBLISHED_AWAITING_MUSE",
        "post_url": "https://cafe.daangn.com/i-gagyeog-mos-c/posts/example",
        "posted_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_id": "273848352",
        "item_id": "19443214873",
    }
    return s


class OneShotRepostTests(unittest.TestCase):
    def test_select_latest_without_mutating_history(self):
        s = state()
        snapshot = repr(s)
        source_id, entry = select_latest_published(s)
        self.assertEqual(source_id, 94884)
        self.assertEqual(entry["product_id"], "273848352")
        self.assertEqual(repr(s), snapshot)

    def test_skip_expired_latest(self):
        s = state()
        s["published"]["94884"]["posted_at_utc"] = (
            datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat()
        with self.assertRaisesRegex(RuntimeError, "EXPIRED"):
            select_latest_published(s)

    def test_replay_build_keeps_original_status(self):
        s = state()
        source = {**deal(94884), "posted_at_utc": s["published"]["94884"]["posted_at_utc"]}
        article = {
            "source_id": 94884,
            "product_id": "273848352",
            "item_id": "19443214873",
            "muse_rewrite_pending": False,
            "candidate_url": "https://link.coupang.com/re/AFFSDP?"
                             "lptag=AF9668915&pageKey=273848352&itemId=19443214873",
        }
        with patch("gh_only.one_shot_test_repost.build_next", return_value=article):
            actual = prepare_test_article(s, source, 94884, s["published"]["94884"])
        self.assertEqual(actual["source_id"], 94884)
        self.assertEqual(s["published"]["94884"]["status"], "PUBLISHED_AWAITING_MUSE")
        self.assertEqual(s["pending"], [])

    def test_replay_rejects_mismatched_item_id(self):
        s = state()
        source = {**deal(94884), "posted_at_utc": s["published"]["94884"]["posted_at_utc"]}
        article = {
            "source_id": 94884,
            "product_id": "273848352",
            "item_id": "123456789",
            "muse_rewrite_pending": False,
            "candidate_url": "https://link.coupang.com/re/AFFSDP?"
                             "lptag=AF9668915&pageKey=273848352&itemId=123456789",
        }
        with patch("gh_only.one_shot_test_repost.build_next", return_value=article):
            with self.assertRaisesRegex(RuntimeError, "PRODUCT_MISMATCH"):
                prepare_test_article(s, source, 94884, s["published"]["94884"])

    def test_non_coupang_rejected(self):
        s = state()
        source = {**deal(94884, kind="external"), "posted_at_utc": s["published"]["94884"]["posted_at_utc"]}
        with self.assertRaisesRegex(RuntimeError, "NOT_COUPANG"):
            prepare_test_article(s, source, 94884, s["published"]["94884"])


if __name__ == "__main__":
    unittest.main()
