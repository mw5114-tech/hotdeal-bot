"""Offline checks: latest means newest Coupang post, not newest *eligible* post."""
import unittest
from datetime import datetime, timedelta, timezone
from gh_only.naver_blog_latest_manual import latest_coupang, manual_source


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
