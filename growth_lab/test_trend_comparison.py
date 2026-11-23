"""Offline tests: no credential, browser, Naver or Coupang network use."""
import unittest
from datetime import datetime, timezone
from growth_lab.trend_comparison import (
    shop_payload, score_group, choose_pair, size_tokens, pair_key,
)


class ComparisonTests(unittest.TestCase):
    def test_payload_is_last_28_complete_days(self):
        now = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
        payload = shop_payload("50000003", ("가습기", "무선 키보드"), now)
        self.assertEqual(payload["endDate"], "2026-11-22")
        self.assertEqual(payload["startDate"], "2026-10-26")
        self.assertEqual(len(payload["keyword"]), 2)

    def test_group_scores_only_verified_relative_indices(self):
        def daily(v, offset):
            from datetime import date, timedelta
            return [{"period": str(date(2026, 10, 26) + timedelta(days=i)),
                     "ratio": v if i < 21 else v + offset}
                    for i in range(28)]
        data = {"results": [
            {"title": "가습기", "data": daily(40, 20)},
            {"title": "보조배터리", "data": daily(20, 0)},
            {"title": "스마트폰", "data": daily(90, 10)},
        ]}
        ranks = score_group(data, ("가습기", "보조배터리"), "IT/가전")
        self.assertEqual([x["keyword"] for x in ranks], ["가습기"])
        self.assertTrue(ranks[0]["relative_index_only"])
        self.assertGreater(ranks[0]["growth_ratio"], 1)

    def test_missing_history_never_promoted(self):
        rows = {"results": [{"title": "가습기",
                            "data": [{"period": "2026-11-22", "ratio": 100}]}]}
        self.assertEqual(score_group(rows, ("가습기",), "IT/가전"), [])

    def test_pair_rejects_different_option_size_and_image(self):
        def offer(pid, name, price, image):
            return {"id": pid, "name": name, "price": price, "image": image}
        offers = [
            offer("11111", "가습기 기본 2L", 20000, "image1"),
            offer("22222", "가습기 기본 4L", 25000, "image2"),
            offer("33333", "가습기 다른회사 2L", 28000, "image3"),
        ]
        self.assertEqual([x["id"] for x in choose_pair(offers)], ["11111", "33333"])
        self.assertIsNone(choose_pair(offers[:2]))

    def test_size_and_key(self):
        self.assertIn("512GB", size_tokens("외장 SSD 512GB"))
        first = {"id": "11111"}
        second = {"id": "22222"}
        self.assertEqual(pair_key("SSD", first, second), pair_key("SSD", second, first))


if __name__ == "__main__":
    unittest.main()
