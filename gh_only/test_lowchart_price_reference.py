"""No-network tests for Lowchart's human-visible price summary."""
import unittest
from gh_only.lowchart_price_reference import parse_reference, build_url

P="6221369038"
I="12453073752"
URL="https://www.lowchart.com/6221369038-12453073752"
TOKENS=("오뚜기","핫케이크","1kg","1개")
BODY="""<html><head><title>오뚜기 핫케이크 믹스, 1kg, 1개 - 5,350원 - 로우차트</title></head><body>
<div>상품번호 6221369038 옵션 12453073752</div>
<div>가격 변동 요약 현재 가격은 5,350원으로, 관측 최저가 3,180원보다 2,170원 높습니다.</div>
<div>현재가 5,350원 관측 최저가 3,180원 관측 최고가 5,880원 관측 평균가 5,531원 최근 변동일 2026-11-01</div>
</body></html>"""


class ReferenceTests(unittest.TestCase):
    def test_route_template(self):
        self.assertEqual(build_url(P,I),URL)
        with self.assertRaises(ValueError):
            build_url("abc",I)

    def test_correct_option_statistics(self):
        r=parse_reference(BODY,URL,P,I,TOKENS,3180)
        self.assertTrue(r["usable_historical_reference"])
        self.assertEqual(r["observed_average_krw"],5531)
        self.assertEqual(r["observed_low_krw"],3180)
        self.assertEqual(r["observed_current_krw"],5350)
        self.assertEqual(r["source_last_change_date"],"2026-11-01")
        self.assertAlmostEqual(r["reference_discount_vs_average_pct"],42.5)
        self.assertFalse(r["official_normal_price"])

    def test_missing_item_in_source_rejected(self):
        r=parse_reference(BODY.replace(I,"00000000000"),URL,P,I,TOKENS,3180)
        self.assertFalse(r["usable_historical_reference"])
        self.assertEqual(r["reason"],"MISSING_EXACT_IDS_IN_SOURCE")

    def test_wrong_option_in_title_rejected(self):
        r=parse_reference(BODY.replace("1kg, 1개","500g, 2개"),URL,P,I,TOKENS,3180)
        self.assertFalse(r["usable_historical_reference"])
        self.assertEqual(r["reason"],"OPTION_TITLE_MISMATCH")

    def test_wrong_source_url_rejected(self):
        with self.assertRaises(ValueError):
            parse_reference(BODY,URL+"?x=123",P,I,TOKENS,3180)

    def test_invalid_average_rejected(self):
        r=parse_reference(BODY.replace("관측 평균가 5,531원","관측 평균가 100,000원"),URL,P,I,TOKENS,3180)
        self.assertFalse(r["usable_historical_reference"])
        self.assertEqual(r["reason"],"INVALID_OBSERVED_PRICE_ORDER")

    def test_no_price_summary_rejected(self):
        r=parse_reference(BODY.replace("관측 평균가","관측 미상가"),URL,P,I,TOKENS,3180)
        self.assertFalse(r["usable_historical_reference"])
        self.assertEqual(r["reason"],"VISIBLE_HISTORY_SUMMARY_NOT_FOUND")

    def test_not_claim_normal_price(self):
        r=parse_reference(BODY,URL,P,I,TOKENS,3180)
        self.assertFalse(r["official_normal_price"])
        self.assertEqual(r["history_window"],"entire_tracked_history_unspecified_days")

if __name__=="__main__":
    unittest.main()
