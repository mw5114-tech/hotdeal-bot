"""Pure offline guards for public historical price probes."""
import unittest
from gh_only.price_history_probe import (
    parse_history, url_ids_match, probe, DAY_HISTORY
)

EXACT = "https://www.coupang.com/vp/products/6221369038?itemId=12453073752"
OTHER = "https://www.coupang.com/vp/products/6221369038?itemId=00000000000"
SAMPLE = """<html><head><title>오뚜기 핫케이크 믹스, 1kg, 1개 - 핫덕</title></head>
<body><h1>오뚜기 핫케이크 믹스, 1kg, 1개</h1>
<p>최근 184일 동안 이 상품은 3,180원 ~ 4,750원 사이였고, 평균은 4,266원이에요.</p>
<a href="https://www.coupang.com/vp/products/6221369038?itemId=12453073752">구매</a>
</body></html>"""


class PriceHistoryTests(unittest.TestCase):
    def test_exact_id_and_item_valid(self):
        self.assertTrue(url_ids_match(EXACT))
        self.assertFalse(url_ids_match(OTHER))
        self.assertFalse(url_ids_match("https://www.coupang.com/vp/products/6221369038"))
        self.assertFalse(url_ids_match("https://evil.example/vp/products/6221369038?itemId=12453073752"))

    def test_labeled_historical_avg_and_exact_item(self):
        d = parse_history(SAMPLE, "hotduk", "KNOWN_HOTDEAL_PAGE")
        self.assertTrue(d["history_found"])
        self.assertTrue(d["source_item_id_verified"])
        self.assertEqual(d["historical_average_krw"], 4266)
        self.assertEqual(d["historical_min_krw"], 3180)
        self.assertEqual(d["historical_max_krw"], 4750)
        self.assertEqual(d["historical_days"], 184)

    def test_historical_avg_without_matching_item_not_verified(self):
        d = parse_history(SAMPLE.replace(EXACT, OTHER), "hotduk", "KNOWN_HOTDEAL_PAGE")
        self.assertTrue(d["history_found"])
        self.assertFalse(d["source_item_id_verified"])
        self.assertFalse(d["normal_price_verified_for_target_item"])

    def test_hypothetical_lowchart_page_not_claimed_verified(self):
        d = parse_history(SAMPLE, "lowchart", "ID_TEMPLATE_UNCONFIRMED")
        self.assertFalse(d["history_found"])
        self.assertEqual(d["reason"], "SPECULATIVE_PRODUCT_ROUTE_NO_VERIFIED_ITEM_MATCH")

    def test_jigeumsa_home_does_not_infer_item_price(self):
        d = parse_history("<p>오늘의 특가</p><p>최저 3,180원 평균 4,266원</p>",
                          "jigeumsa", "HOME_ONLY_NO_ITEM_SEARCH")
        self.assertFalse(d["history_found"])
        self.assertFalse(d["source_item_id_verified"])

    def test_invalid_day_range_not_price(self):
        html = SAMPLE.replace("3,180원 ~ 4,750원", "5,000원 ~ 4,750원")
        d = parse_history(html, "hotduk", "KNOWN_HOTDEAL_PAGE")
        self.assertFalse(d["history_found"])

    def test_unknown_domain_not_fetched(self):
        calls = []
        def op(*args, **kwargs):
            calls.append(1)
            raise AssertionError("should not fetch")
        with self.assertRaises(ValueError):
            probe("hotduk", "https://evil.example/deal/156817", "KNOWN_HOTDEAL_PAGE", opener=op)
        self.assertEqual(calls, [])

    def test_no_price_from_javascript(self):
        html = "<script>최근 184일 동안 이 상품은 3,180원 ~ 4,750원 사이였고, 평균은 4,266원</script>"
        d = parse_history(html, "hotduk", "KNOWN_HOTDEAL_PAGE")
        self.assertFalse(d["history_found"])


if __name__ == "__main__":
    unittest.main()
