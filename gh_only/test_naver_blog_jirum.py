"""Offline regression checks for Naver queue segregation and safe source filtering."""
from datetime import datetime, timedelta, timezone
import unittest

from gh_only.naver_blog_jirum import (
    ingest, validate_state, source_fresh, validate_naver_link_subid,
    naver_title, archive_stale_claim, NAVER_TITLE_MAX_UTF16_UNITS,
)


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
    def test_long_title_is_safely_shortened_and_keeps_discount(self):
        title = "[쿠팡🔥핫딜] " + ("테팔 인덕션 매직핸즈 스테인리스 프라이팬 22cm + " * 6) + "[30%할인]"
        actual = naver_title(title)
        self.assertLessEqual(len(actual.encode("utf-16-le")) // 2, NAVER_TITLE_MAX_UTF16_UNITS)
        self.assertTrue(actual.startswith("[쿠팡🔥핫딜] "))
        self.assertTrue(actual.endswith("[30%할인]"))
        self.assertIn("…", actual)
        self.assertEqual(naver_title("[쿠팡🔥핫딜] 짧은 상품 [25%할인]"),
                         "[쿠팡🔥핫딜] 짧은 상품 [25%할인]")

    def test_long_title_with_emoji_stays_within_utf16_limit(self):
        original = "[쿠팡🔥핫딜] " + "📱🥘" * 55 + " [20%할인]"
        actual = naver_title(original)
        self.assertLessEqual(len(actual.encode("utf-16-le")) // 2, NAVER_TITLE_MAX_UTF16_UNITS)
        self.assertTrue(actual.endswith(" [20%할인]"))

    def test_abandoned_claim_quarantine_never_replays(self):
        s = state()
        row = deal(95496, minutes=90)
        ingest(s, [row], reference=datetime.now(timezone.utc) - timedelta(minutes=85))
        s["in_flight"] = {
            "id":95496, "status":"REVIEW_REQUIRED",
            "run_id":"42712155025",
            "at": (datetime.now(timezone.utc) - timedelta(minutes=80)).isoformat()
        }
        validate_state(s)
        self.assertTrue(archive_stale_claim(s))
        self.assertIsNone(s["in_flight"])
        self.assertEqual(s["pending"], [])
        self.assertEqual(s["skipped"]["95496"]["reason"], "PUBLISH_OUTCOME_UNCONFIRMED_NO_AUTORETRY")
        self.assertEqual(s["skipped"]["95496"]["claimed_run_id"], "42712155025")

    def test_recent_claim_remains_locked(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {
            "id":95496, "status":"REVIEW_REQUIRED",
            "run_id":"999",
            "at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        }
        self.assertFalse(archive_stale_claim(s))
        self.assertEqual(s["pending"][0]["id"], 95496)

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

    def test_naverblog_subid_is_required(self):
        good = ("https://link.coupang.com/re/AFFSDP?"
                "lptag=AF9668915&subid=naverblog&pageKey=123&itemId=456")
        self.assertIsNone(validate_naver_link_subid(good))
        with self.assertRaisesRegex(ValueError, "NAVER_BLOG_SUBID_NOT_NAVERBLOG"):
            validate_naver_link_subid(good.replace("subid=naverblog", "subid=daangncafe"))
        with self.assertRaisesRegex(ValueError, "NAVER_BLOG_SUBID_NOT_NAVERBLOG"):
            validate_naver_link_subid(good.replace("subid=naverblog", "subid=naverblog2"))
        with self.assertRaisesRegex(ValueError, "NAVER_BLOG_SUBID_NOT_NAVERBLOG"):
            validate_naver_link_subid(good.replace("subid=naverblog&", ""))

    def test_separate_state_schema(self):
        s = state()
        s["schema"] = "daangn-jirum-manual-fifo-v1"
        with self.assertRaisesRegex(ValueError, "NAVER_FIFO_INVALID_SCHEMA"):
            validate_state(s)


if __name__ == "__main__":
    unittest.main()
