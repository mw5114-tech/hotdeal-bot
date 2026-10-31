"""Offline contract tests for the default Telegram-price + Lowchart format."""
import unittest

from gh_only.default_deal_format import (
    nearest_five_discount, same_named_option, validated_history, create_default_preview,
)
from gh_only.pipeline import price_info
from gh_only.lowchart_price_reference import parse_reference, build_url

PID = "9217210892"
IID = "27232840915"
OG = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개 - 즉석카레 | 쿠팡"
LOW_TITLE = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개 - 31,340원 - 로우차트"
LOW_BODY = f"""<html><title>{LOW_TITLE}</title><body>
productId={PID}, itemId={IID} 가격 변동 요약
현재가 31,340원 관측 최저가 20,820원 관측 최고가 48,590원
관측 평균가 34,575원 최근 변동일 2026-10-31</body></html>"""


class DefaultFormatTests(unittest.TestCase):
    def test_65_percent_rounds_to_65(self):
        self.assertEqual(nearest_five_discount(12050, 34575), 65)

    def test_42point5_rounds_up_to_45(self):
        self.assertEqual(nearest_five_discount(3180, 5531), 45)

    def test_boundary_round_half_up(self):
        self.assertEqual(nearest_five_discount(7750, 10000), 25)
        self.assertEqual(nearest_five_discount(8250, 10000), 20)

    def test_negative_equal_or_absent_prices_make_no_discount(self):
        for sale, avg in [(None, 10000), (0, 10000), (10000, None),
                          (11000, 10000), (10000, 10000), (1000, 0)]:
            self.assertIsNone(nearest_five_discount(sale, avg))

    def test_tiny_discount_is_not_labeled_zero(self):
        self.assertIsNone(nearest_five_discount(9900, 10000))

    def test_very_large_discount_not_labeled_100(self):
        self.assertEqual(nearest_five_discount(100, 10000), 95)

    def test_both_product_titles_match_with_source_suffixes(self):
        self.assertTrue(same_named_option(OG, LOW_TITLE))
        self.assertFalse(same_named_option(OG, LOW_TITLE.replace("20개", "2개")))
        self.assertFalse(same_named_option(OG, LOW_TITLE.replace("230g", "300g")))
        self.assertFalse(same_named_option(None, LOW_TITLE))

    def test_telegram_sale_first_then_comparison(self):
        caption = create_default_preview(12050, "휘카레 소스", 34575, 123)
        self.assertEqual(caption["caption"].splitlines(), [
            "💰 뽐질 특가 12,050원",
            "📉 로우차트 관측 평균가 대비 약 65% 할인",
            "📊 로우차트 관측 평균가 34,575원",
            "📦 휘카레 소스",
        ])
        self.assertEqual(caption["rounded_discount_step"], 5)
        self.assertFalse(caption["official_normal_price_quoted"])
        self.assertFalse(caption["auto_publish_ready"])

    def test_missing_mean_leaves_price_not_fake_discount(self):
        preview = create_default_preview(12050, "휘카레 소스", None)
        self.assertEqual(preview["caption"],
                         "💰 뽐질 특가 12,050원\n📦 휘카레 소스")
        self.assertIsNone(preview["approx_discount_vs_historical_mean_pct"])

    def test_mean_below_sale_doesnt_claim_discount(self):
        preview = create_default_preview(12050, "휘카레 소스", 10000)
        self.assertNotIn("% 할인", preview["caption"])
        self.assertIn("관측 평균가 10,000원", preview["caption"])

    def test_source_post_discounts_do_not_override_lowchart(self):
        raw = """🔥 무지성 12,050원 (59.8% 할인)
휘카레 셰프 오리지널 소스 순한맛, 230g, 20개
- 정상가: 30,000원
- 할인: 17,950원"""
        price = price_info(raw)
        self.assertEqual(price["sale_price_krw"], 12050)
        preview = create_default_preview(price["sale_price_krw"], "휘카레", 34575)
        self.assertIn("약 65% 할인", preview["caption"])
        self.assertNotIn("59.8%", preview["caption"])
        self.assertNotIn("30,000원", preview["caption"])

    def test_exact_id_and_same_option_history_used(self):
        hist = parse_reference(LOW_BODY, build_url(PID, IID), PID, IID,
                               ("휘카레", "230g", "20개"), 12050)
        self.assertTrue(hist["usable_historical_reference"])
        self.assertEqual(validated_history(hist, PID, IID, OG), 34575)

    def test_wrong_item_history_rejected(self):
        hist = parse_reference(LOW_BODY, build_url(PID, IID), PID, IID,
                               ("휘카레", "230g", "20개"), 12050)
        hist["item_id"] = "9999999999"
        self.assertIsNone(validated_history(hist, PID, IID, OG))

    def test_wrong_option_history_rejected(self):
        hist = parse_reference(LOW_BODY, build_url(PID, IID), PID, IID,
                               ("휘카레", "230g", "20개"), 12050)
        hist["page_title"] = hist["page_title"].replace("20개", "2개")
        self.assertIsNone(validated_history(hist, PID, IID, OG))

    def test_unverified_history_not_used(self):
        hist = parse_reference(LOW_BODY, build_url(PID, IID), PID, IID,
                               ("휘카레", "230g", "20개"), 12050)
        hist["usable_historical_reference"] = False
        self.assertIsNone(validated_history(hist, PID, IID, OG))


if __name__ == "__main__":
    unittest.main()
