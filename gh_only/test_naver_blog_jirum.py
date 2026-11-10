"""Offline regression checks for Naver queue segregation and safe source filtering."""
from datetime import datetime, timedelta, timezone
import unittest
from copy import deepcopy
from unittest.mock import patch

from gh_only.naver_blog_jirum import (
    ingest, validate_state, source_fresh, validate_naver_link_subid,
    naver_title, archive_stale_claim, NAVER_TITLE_MAX_UTF16_UNITS,
    NaverStateConflict, scan, claim_before_publish, checkpoint_published,
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


class ConflictStore:
    """Simulates a second workflow advancing the GitHub branch head."""
    def __init__(self, initial=None, conflicts=1):
        self.current = deepcopy(initial if initial is not None else state())
        self.conflicts = conflicts
        self.write_count = 0
        self.read_count = 0

    def read(self):
        self.read_count += 1
        return deepcopy(self.current), f"sha-{self.read_count}"

    def write(self, next_state, sha, message):
        self.write_count += 1
        if self.write_count <= self.conflicts:
            # The OTHER workflow updated state independently. A fresh
            # read/recompute must preserve this new entry.
            self.current["published"]["95490"] = {
                "url": "https://blog.naver.com/mw5114/1",
                "at": "2026-11-10T00:00:00Z",
            }
            raise NaverStateConflict("NAVER_STATE_KNOWN_409_REBASE")
        self.current = deepcopy(next_state)


class NaverQueueTests(unittest.TestCase):
    def test_known_409_scan_rebases_and_preserves_other_state(self):
        store = ConflictStore()
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            result = scan(store)
        self.assertEqual(result["new"], 1)
        self.assertEqual(store.write_count, 2)
        self.assertEqual(store.current["pending"][0]["id"], 95496)
        self.assertIn("95490", store.current["published"])

    def test_known_409_before_claim_rebases_without_post(self):
        s = state()
        ingest(s, [deal(95496)])
        store = ConflictStore(initial=s, conflicts=1)
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            claim_before_publish(store, 95496)
        self.assertEqual(store.write_count, 2)
        self.assertEqual(store.current["in_flight"]["id"], 95496)
        self.assertIn("95490", store.current["published"])

    def test_known_409_post_receipt_retries_only_state_commit(self):
        s = state()
        ingest(s, [deal(95496)])
        s["in_flight"] = {"id":95496, "status":"REVIEW_REQUIRED",
                          "run_id":"42738963892",
                          "at":datetime.now(timezone.utc).isoformat()}
        store = ConflictStore(initial=s, conflicts=1)
        link = "https://blog.naver.com/mw5114/224471000000"
        with patch("gh_only.naver_blog_jirum.time.sleep"):
            checkpoint_published(store, 95496, link)
        self.assertIsNone(store.current["in_flight"])
        self.assertEqual(store.current["pending"], [])
        self.assertEqual(store.current["published"]["95496"]["url"], link)
        self.assertIn("95490", store.current["published"])
        self.assertEqual(store.write_count, 2)

    def test_409_retry_is_bounded_and_does_not_touch_live_publish(self):
        store = ConflictStore(conflicts=10)
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            with self.assertRaises(NaverStateConflict):
                scan(store)
        self.assertEqual(store.write_count, 5)

    def test_ambiguous_write_error_is_never_retried(self):
        class UncertainStore(ConflictStore):
            def write(self, next_state, sha, message):
                self.write_count += 1
                raise RuntimeError("NAVER_STATE_COMMIT_UNCERTAIN")
        store = UncertainStore()
        with patch("gh_only.naver_blog_jirum.old.collect_since",
                   return_value=[deal(95496)]), patch(
                   "gh_only.naver_blog_jirum.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "COMMIT_UNCERTAIN"):
                scan(store)
        self.assertEqual(store.write_count, 1)

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
