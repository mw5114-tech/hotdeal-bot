"""Offline checks: latest means newest Coupang post, not newest *eligible* post."""
import unittest
from datetime import datetime, timedelta, timezone
from gh_only.naver_blog_latest_manual import (
    latest_coupang, manual_source, claim_selected_latest, commit_selected_latest
)
from gh_only.test_naver_blog_jirum import state, deal
from gh_only.naver_blog_jirum import ingest, validate_state


class LatestOneShotTests(unittest.TestCase):
    def row(self, i, *, kind="coupang_shortlink", price=9900, hours=1):
        return {
            "source_message_id": i,
            "source_post_url": f"https://t.me/jirum/{i}",
            "url_kind": kind,
            "posted_at_utc": (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).isoformat(),
            "sale_price_krw": price,
            "original_purchase_url": "https://link.coupang.com/a/ABC",
            "title": "테스트 상품", "source_text": "테스트 상품 9,900원",
        }

    def test_latest_coupang_skips_toss_but_not_missing_price(self):
        rows = [
            self.row(100),
            self.row(102, kind="other"),
            self.row(101, price=None),
        ]
        self.assertEqual(latest_coupang(rows)["source_message_id"], 101)
        with self.assertRaisesRegex(RuntimeError, "PRICE_MISSING"):
            manual_source(latest_coupang(rows))

    def test_latest_claim_can_complete_newest_without_removing_oldest(self):
        s = state()
        ingest(s, [deal(95496), deal(95497)])
        newer = dict(s["pending"][1])
        claim_selected_latest(s, newer, "owner-test")
        self.assertEqual([x["id"] for x in s["pending"]], [95496, 95497])
        self.assertEqual(s["in_flight"]["id"], 95497)
        commit_selected_latest(s, newer, "https://blog.naver.com/mw5114/224474999999")
        self.assertEqual([x["id"] for x in s["pending"]], [95496])
        self.assertIn("95497", s["published"])
        self.assertIsNone(s["in_flight"])
        validate_state(s)

    def test_latest_claim_rejects_replay_and_source_mismatch(self):
        s = state()
        ingest(s, [deal(95496)])
        source = dict(s["pending"][0])
        bad = {**source, "sale_price_krw": source["sale_price_krw"]+10}
        with self.assertRaisesRegex(RuntimeError, "QUEUE_MISMATCH"):
            claim_selected_latest(s, bad, "owner-test")
        self.assertIsNone(s["in_flight"])
        claim_selected_latest(s, source, "owner-test")
        commit_selected_latest(s, source, "https://blog.naver.com/mw5114/224474999999")
        with self.assertRaisesRegex(RuntimeError, "ALREADY_HANDLED"):
            claim_selected_latest(s, source, "owner-test")

    def test_latest_claim_cannot_replay_scanned_missing(self):
        s = state()
        ingest(s, [deal(95496)])
        source = {**s["pending"][0], "id": 95495, "source_message_id": 95495}
        with self.assertRaisesRegex(RuntimeError, "SCANNED_BUT_NOT_QUEUED"):
            claim_selected_latest(s, source, "owner-test")

    def test_target_exact_telegram_id_even_when_newer_exists(self):
        rows = [self.row(95725), self.row(95728), self.row(95724)]
        self.assertEqual(latest_coupang(rows, target_id=95725)["source_message_id"], 95725)
        with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_FOUND"):
            latest_coupang(rows, target_id=95727)
        with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_FOUND"):
            latest_coupang([self.row(95725, kind="other")], target_id=95725)
        with self.assertRaisesRegex(RuntimeError, "TARGET_NOT_FOUND"):
            latest_coupang([self.row(95725), self.row(95725)], target_id=95725)
        with self.assertRaisesRegex(RuntimeError, "TARGET_ID_INVALID"):
            latest_coupang(rows, target_id=-1)

    def test_only_one_latest(self):
        self.assertEqual(latest_coupang([self.row(10), self.row(11)])["source_message_id"], 11)
        self.assertIsNone(latest_coupang([self.row(11, kind="other")]))

    def test_manual_source_accepts_same_day_only(self):
        output = manual_source(self.row(12345, hours=9))
        self.assertEqual(output["id"], 12345)
        self.assertEqual(output["sale_price_krw"], 9900)
        with self.assertRaisesRegex(RuntimeError, "OLDER_THAN_24H"):
            manual_source(self.row(12345, hours=25))

    def test_missing_timestamp_fails_closed(self):
        row = self.row(12345)
        row["posted_at_utc"] = None
        with self.assertRaisesRegex(RuntimeError, "TIME_MISSING"):
            manual_source(row)

    def test_wrong_purchase_domain_fails_closed(self):
        row = self.row(12345)
        row["original_purchase_url"] = "https://toss.shopping/_m/example"
        with self.assertRaisesRegex(RuntimeError, "COUPANG_URL_MISSING"):
            manual_source(row)

if __name__ == "__main__":
    unittest.main()
