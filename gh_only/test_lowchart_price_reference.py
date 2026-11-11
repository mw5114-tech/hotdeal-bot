"""No-network tests for Lowchart's human-visible price summary."""
import unittest
from gh_only.lowchart_price_reference import parse_reference, build_url
from gh_only.default_deal_format import (
    validated_history, create_default_preview, nearest_five_discount,
)

P="6221369038"
I="12453073752"
URL="https://www.lowchart.com/6221369038-12453073752"
TOKENS=("오뚜기","핫케이크","1kg","1개")
BODY="""<html><head><title>오뚜기 핫케이크 믹스, 1kg, 1개 - 5,350원 - 로우차트</title></head><body>
<div>상품번호 6221369038 옵션 12453073752</div>
<div>가격 변동 요약 현재 가격은 5,350원으로, 관측 최저가 3,180원보다 2,170원 높습니다.</div>
<div>현재가 5,350원 관측 최저가 3,180원 관측 최고가 5,880원 관측 평균가 5,531원 최근 변동일 2026-11-01</div>
</body></html>"""


# Representative HTML from the currently visible Lowchart item presentation:
# the page has no old "현재가 ... 관측 평균가 ... 최근 변동일" sequence.
NEW_PID = "7141704232"
NEW_IID = "17931956102"
NEW_URL = build_url(NEW_PID, NEW_IID)
NEW_NAME = "토종마을 국산 황기 절단, 300g, 1개"
NEW_BODY = f"""<html><head><title>{NEW_NAME} - 15,300원 - 로우차트 - 쿠팡 가격 변동 추적</title>
</head><body>
<div data-product-id="{NEW_PID}" data-item-id="{NEW_IID}"></div>
<div>지금 사도 될까?</div><div>−1%</div>
<p>{NEW_NAME}의 쿠팡 현재가는 15,300원이며, 로우차트가 관측한 역대 최저가는
12,600원, 최고가는 15,840원입니다. 최근 3주 평균 15,460원보다 1% 저렴합니다.
마지막 가격 변동은 11월 4일(−540원)입니다. (11월 4일 기준)</p>
<div>추천 상품 A는 1,000원에 판매 중입니다.</div>
</body></html>"""


class ReferenceTests(unittest.TestCase):
    def test_current_lowchart_visible_summary_restores_average_and_discount(self):
        ref = parse_reference(
            NEW_BODY, NEW_URL, NEW_PID, NEW_IID, ("토종마을", "300g", "1개"), 11350
        )
        self.assertTrue(ref["usable_historical_reference"])
        self.assertEqual(ref["reason"], "EXACT_ITEM_VISIBLE_RECENT_AVERAGE_MATCH")
        self.assertEqual(ref["history_window"], "recent_3_weeks")
        self.assertEqual(ref["source_observation_type"],
                         "price_history_recent_week_summary")
        self.assertEqual(ref["observed_low_krw"], 12600)
        self.assertEqual(ref["observed_high_krw"], 15840)
        self.assertEqual(ref["observed_current_krw"], 15300)
        self.assertEqual(ref["observed_average_krw"], 15460)
        self.assertIsNone(ref["source_last_change_date"])
        self.assertEqual(validated_history(ref, NEW_PID, NEW_IID, NEW_NAME), 15460)
        self.assertEqual(nearest_five_discount(11350, 15460), 25)
        preview = create_default_preview(11350, NEW_NAME, 15460, 94960)
        self.assertIn("[25%할인] " + NEW_NAME, preview["caption"])
        self.assertIn("💸 쿠팡 평소 판매가 : 약 15,460원", preview["caption"])
        self.assertIn("🔥 핫딜 특가 : 11,350원 (약 25% 할인", preview["caption"])

    def test_current_average_exact_item_title_must_match_not_other_sku(self):
        # Product IDs are in the current item's page, but the narrative
        # belongs to a different package. Do NOT borrow its mean.
        bad = NEW_BODY.replace(
            NEW_NAME + "의 쿠팡 현재가는",
            "토종마을 국산 황기 절단, 300g, 10개의 쿠팡 현재가는",
        )
        result = parse_reference(bad, NEW_URL, NEW_PID, NEW_IID,
                                 ("토종마을", "300g", "1개"), 11350)
        self.assertFalse(result["usable_historical_reference"])
        self.assertEqual(result["reason"], "VISIBLE_HISTORY_SUMMARY_NOT_FOUND")

    def test_without_mean_verified_lowchart_high_is_still_usable(self):
        from gh_only.default_deal_format import validated_lowchart_observation
        doc = NEW_BODY.replace("최근 3주 평균 15,460원", "최근 평균 확인 중")
        r = parse_reference(doc, NEW_URL, NEW_PID, NEW_IID,
                            ("토종마을", "300g", "1개"), 11350)
        self.assertFalse(r["usable_historical_reference"])
        self.assertTrue(r["visible_exact_item_prices_verified"])
        self.assertEqual(r["observed_high_krw"], 15840)
        self.assertEqual(
            validated_lowchart_observation(r, NEW_PID, NEW_IID, NEW_NAME,
                                           "observed_high_krw"), 15840
        )
        self.assertIsNone(
            validated_lowchart_observation(r, NEW_PID, "11111111111",
                                           NEW_NAME, "observed_high_krw"))
        self.assertIsNone(
            validated_lowchart_observation(r, NEW_PID, NEW_IID,
                                           "다른 상품 2개", "observed_high_krw"))

    def test_current_page_without_mean_does_not_invent_a_discount(self):
        bad = NEW_BODY.replace("최근 3주 평균 15,460원", "최근 평균 확인 중")
        ref = parse_reference(bad, NEW_URL, NEW_PID, NEW_IID,
                              ("토종마을", "300g", "1개"), 11350)
        self.assertFalse(ref["usable_historical_reference"])
        self.assertIsNone(ref["observed_average_krw"])

    def test_current_page_inconsistent_price_order_is_rejected(self):
        bad = NEW_BODY.replace("최근 3주 평균 15,460원", "최근 3주 평균 20,000원")
        ref = parse_reference(bad, NEW_URL, NEW_PID, NEW_IID,
                              ("토종마을", "300g", "1개"), 11350)
        self.assertEqual(ref["reason"], "INVALID_OBSERVED_PRICE_ORDER")
        self.assertFalse(ref["usable_historical_reference"])

    def test_current_page_different_item_id_is_rejected(self):
        bad = NEW_BODY.replace(NEW_IID, "11111111111")
        ref = parse_reference(bad, NEW_URL, NEW_PID, NEW_IID,
                              ("토종마을", "300g", "1개"), 11350)
        self.assertEqual(ref["reason"], "MISSING_EXACT_IDS_IN_SOURCE")

    def test_recent_equal_average_is_still_observed_without_claimed_discount(self):
        name = "풀무원 평양 물냉면 4인분, 1.692kg, 2개"
        pid, iid = "111268957", "19233094153"
        html = (f"<title>{name} - 17,960원 - 로우차트</title>"
                f"<main>productId={pid}, itemId={iid}</main>"
                f"<p>{name}의 쿠팡 현재가는 17,960원이며, "
                "로우차트가 관측한 역대 최저가는 11,010원, "
                "최고가는 18,960원입니다. "
                "최근 3주 평균 17,960원과 같은 수준입니다.</p>")
        ref = parse_reference(html, build_url(pid, iid), pid, iid,
                              ("풀무원", "1.692kg", "2개"), 17960)
        self.assertTrue(ref["usable_historical_reference"])
        self.assertEqual(ref["observed_average_krw"], 17960)
        self.assertIsNone(nearest_five_discount(17960, 17960))

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
