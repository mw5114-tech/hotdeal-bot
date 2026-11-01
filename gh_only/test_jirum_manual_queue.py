"""Offline FIFO regression tests. Never contact Telegram, GitHub or Daangn."""
import copy
import unittest
from unittest.mock import patch

from gh_only.jirum_manual_queue import (
    START_AFTER, SCHEMA, collect_since, finish, ingest, scan, validate_state,
)


def baseline():
    return {
        "schema": SCHEMA, "last_scanned_id": START_AFTER,
        "pending": [], "in_flight": None,
        "published": {
            "94737": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94738": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
            "94743": {"status": "ALREADY_PUBLISHED_BEFORE_QUEUE"},
        },
        "skipped_non_coupang": 0,
    }


def deal(mid, kind="coupang_shortlink", price=12300):
    url = (f"https://link.coupang.com/a/example{mid}"
           if kind == "coupang_shortlink" else "https://example.com/item")
    return {
        "source_message_id": mid,
        "source_post_url": f"https://t.me/jirum/{mid}",
        "original_purchase_url": url,
        "url_kind": kind,
        "source_text": f"상품번호 {mid}\n특가 {price}원",
        "title": f"상품번호 {mid}",
        "sale_price_krw": price,
    }


class FakeStore:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.writes = []

    def read(self):
        return copy.deepcopy(self.value), "sha-test"

    def write(self, state, sha, message):
        if sha != "sha-test":
            raise ValueError("sha mismatch")
        validate_state(state)
        self.value = copy.deepcopy(state)
        self.writes.append(message)


class ManualQueueTests(unittest.TestCase):
    def test_fifo_and_non_coupang_filter(self):
        state, n, ignored = ingest(baseline(), [
            deal(94747), deal(94745, "external"), deal(94746),
            deal(94744, "external")
        ])
        self.assertEqual(n, 2)
        self.assertEqual(ignored, 2)
        self.assertEqual([x["id"] for x in state["pending"]], [94746, 94747])
        self.assertEqual(state["last_scanned_id"], 94747)

    def test_no_new_messages_no_publish(self):
        store = FakeStore(baseline())
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["status"], "NO_NEW_COUPANG_DEAL")
        self.assertFalse(result["has_deal"])
        self.assertEqual(store.writes, [])

    def test_external_only_advances_watermark_without_post(self):
        st, count, ignored = ingest(baseline(), [deal(94744, "external")])
        self.assertEqual((count, ignored), (0, 1))
        self.assertEqual(st["pending"], [])
        self.assertEqual(st["last_scanned_id"], 94744)

    def test_coupang_backlog_oldest_first_one_at_a_time(self):
        st, _, _ = ingest(baseline(), [deal(94744), deal(94745), deal(94746)])
        store = FakeStore(st)
        store.value["in_flight"] = {
            "id": 94744, "status": "CLAIMED",
            "run_id": "run1", "at": "now",
        }
        finish(store, 94744, "run1", {
            "status": "POST_PUBLISHED_AND_FORMAT_VERIFIED",
            "post_url": "https://cafe.daangn.com/i-gagyeog-mos-c/posts/posted44",
        })
        self.assertEqual([x["id"] for x in store.value["pending"]], [94745, 94746])
        self.assertEqual(store.value["published"]["94744"]["status"], "PUBLISHED_VERIFIED")
        self.assertIsNone(store.value["in_flight"])
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["source_id"], 94745)
        self.assertEqual(result["pending"], 2)

    def test_unverified_after_attempt_blocks_all_future_work(self):
        state, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        state["in_flight"] = {"id": 94744, "status": "CLAIMED", "run_id": "run9", "at": "now"}
        store = FakeStore(state)
        finish(store, 94744, "run9", {
            "status": "PUBLISH_UNCERTAIN_DO_NOT_RETRY",
            "submit_attempted": True,
        })
        self.assertEqual(store.value["in_flight"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(store.value["pending"][0]["id"], 94744)
        with patch("gh_only.jirum_manual_queue.collect_since") as fetch:
            result = scan(store)
        fetch.assert_not_called()
        self.assertFalse(result["has_deal"])

    def test_aborted_before_submit_retries_same_head_only(self):
        st, _, _ = ingest(baseline(), [deal(94744), deal(94745)])
        st["in_flight"] = {"id": 94744, "status": "CLAIMED", "run_id": "run10", "at": "now"}
        store = FakeStore(st)
        finish(store, 94744, "run10", {
            "status": "ABORTED_BEFORE_SUBMIT", "submit_attempted": False,
        })
        self.assertIsNone(store.value["in_flight"])
        self.assertEqual([r["id"] for r in store.value["pending"]], [94744, 94745])
        with patch("gh_only.jirum_manual_queue.collect_since", return_value=[]):
            result = scan(store)
        self.assertEqual(result["source_id"], 94744)

    def test_do_not_reprocess_already_published_id(self):
        st, n, _ = ingest(baseline(), [deal(94743), deal(94744)])
        self.assertEqual(n, 1)
        st2, n2, _ = ingest(st, [deal(94743), deal(94744)])
        self.assertEqual(n2, 0)
        self.assertEqual([x["id"] for x in st2["pending"]], [94744])

    def test_contradictory_in_flight_and_published_is_rejected(self):
        st, _, _ = ingest(baseline(), [deal(94744)])
        st["published"]["94744"] = {"status": "PUBLISHED_VERIFIED"}
        with self.assertRaisesRegex(ValueError, "PUBLISHED_ITEM_STILL_QUEUED"):
            validate_state(st)

    def test_collect_since_reads_older_pages_without_skipping_backlog(self):
        lookup = {
            "latest": [deal(94749), deal(94750)],
            "older94749": [deal(94746), deal(94747), deal(94748)],
            "older94746": [deal(94742), deal(94743), deal(94744), deal(94745)],
        }
        with patch("gh_only.jirum_manual_queue.enrich_posts",
                   side_effect=lambda html, max_posts: lookup[html]):
            pages = collect_since(94743, latest_fetch=lambda: "latest",
                                  before_fetch=lambda x: "older" + str(x))
        self.assertEqual([x["source_message_id"] for x in pages],
                         [94744, 94745, 94746, 94747, 94748, 94749, 94750])

    def test_history_not_complete_is_error_not_skipped(self):
        with patch("gh_only.jirum_manual_queue.enrich_posts",
                   return_value=[deal(94800)]):
            with self.assertRaisesRegex(RuntimeError, "HISTORY_NOT_ADVANCING"):
                collect_since(94743, latest_fetch=lambda: "latest",
                              before_fetch=lambda x: "same", max_pages=3)

    def test_oversized_fifo_does_not_advance_cursor(self):
        with self.assertRaisesRegex(RuntimeError, "OVER_LIMIT"):
            ingest(baseline(), [deal(i) for i in range(94744, 95046)])


if __name__ == "__main__":
    unittest.main()
