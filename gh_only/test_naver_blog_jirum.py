"""Offline regression checks for Naver queue segregation and safe source filtering."""
from datetime import datetime, timedelta, timezone
import unittest

from gh_only.naver_blog_jirum import ingest, validate_state, source_fresh


def state():
    return {
        "schema": "naver-blog-jirum-fifo-v1",
        "last_scanned_id": 95495, "pending": [], "published": {},
        "skipped": {}, "in_flight": None,
    }


def deal(source_id, *, price=11900, minutes=10, kind="coupang_shortlink"):
    posted = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    return {
        "source_message_id": source_id, "source_post_url": f"https://t.me/jirum/{source_id}",
        "original_purchase_url": "https://link.coupang.com/a/example",
        "source_text": "샘플 가격 11,900원", "title": "샘플",
        "sale_price_krw": price, "url_kind": kind, "posted_at_utc": posted,
    }


class NaverQueueTests(unittest.TestCase):
    def test_new_fresh_deal_enqueued_once(self):
        s = state()
        n, expired = ingest(s, [deal(95497), deal(95496)])
        self.assertEqual((n, expired), (2, 0))
        self.assertEqual([x["id"] for x in s["pending"]], [95496, 95497])
        self.assertEqual(s["last_scanned_id"], 95497)
        again, _ = ingest(s, [deal(95497)])
        self.assertEqual(again, 0)
        self.assertEqual(len(s["pending"]), 2)

    def test_no_stale_or_unknown_timestamp(self):
        s = state()
        stale = deal(95496, minutes=120)
        missing = deal(95497)
        missing["posted_at_utc"] = None
        n, _ = ingest(s, [stale, missing])
        self.assertEqual(n, 0)
        self.assertEqual(s["pending"], [])
        self.assertEqual(len(s["skipped"]), 2)

    def test_price_missing_and_non_coupang_suppressed(self):
        s = state()
        n, _ = ingest(s, [deal(95496, price=None), deal(95497, kind="other")])
        self.assertEqual(n, 0)
        self.assertFalse(s["pending"])
        self.assertEqual(s["skipped"]["95496"]["reason"], "PRICE_NOT_FOUND")

    def test_prior_watermark_never_backfilled(self):
        s = state()
        n, _ = ingest(s, [deal(95493), deal(95494), deal(95495)])
        self.assertEqual(n, 0)
        self.assertEqual(s["last_scanned_id"], 95495)

    def test_claim_must_reference_pending(self):
        s = state()
        s["in_flight"] = {"id": 95496, "status": "REVIEW_REQUIRED"}
        with self.assertRaisesRegex(ValueError, "NAVER_FIFO_BAD_CLAIM"):
            validate_state(s)

    def test_timestamp_not_inferred(self):
        self.assertFalse(source_fresh({"posted_at_utc": None}))

    def test_separate_state_schema(self):
        s = state()
        s["schema"] = "daangn-jirum-manual-fifo-v1"
        with self.assertRaisesRegex(ValueError, "NAVER_FIFO_INVALID_SCHEMA"):
            validate_state(s)


if __name__ == "__main__":
    unittest.main()
