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

    def test_jjawang_8_short_exact_name_lowchart_observed_average_is_kept(self):
        # The 2026-11-11 real source #95749 was incorrectly discarded
        # because "짜왕8개" has only four normalized characters.
        pid, iid = "788073", "17910000282"
        title = "짜왕, 8개 - 6,470원 - 로우차트 - 쿠팡 가격 변동 추적"
        html = (f'<html><head><title>{title}</title></head><body>'
                f'<div data-product-id="{pid}" data-item-id="{iid}"></div>'
                '<p>짜왕, 8개의 쿠팡 현재가는 6,470원이며, '
                '로우차트가 관측한 역대 최저가는 6,470원, '
                '최고가는 10,600원입니다. '
                '최근 3주 평균 8,857원보다 27% 저렴합니다.</p>'
                '</body></html>')
        ref = parse_reference(html, build_url(pid, iid), pid, iid,
                              ("짜왕", "8개"), 6470)
        self.assertTrue(ref["usable_historical_reference"])
        self.assertEqual(ref["observed_average_krw"], 8857)
        self.assertEqual(validated_history(ref, pid, iid, "짜왕, 8개"), 8857)
        self.assertEqual(nearest_five_discount(6470, 8857), 25)
        self.assertIn("평소 판매가 : 약 8,857원",
                      create_default_preview(6470, "짜왕, 8개", 8857)["caption"])
        # Different short option, even on a superficially similar page:
        self.assertFalse(same_named_option("짜왕, 16개", title))
        self.assertFalse(same_named_option("8개", title))
        self.assertFalse(same_named_option("짜왕", title))
        self.assertIsNone(validated_history(ref, pid, "17910000283", "짜왕, 8개"))

    def test_verified_tiny_rates_are_reported(self):
        c = create_default_preview(9800, "소금 1개", 10000)
        self.assertEqual(c["approx_discount_vs_reference_pct"], 2)
        self.assertIn("[2%할인]", c["caption"])
        tiny = create_default_preview(9990, "소금 1개", 10000)
        self.assertIsNone(tiny["approx_discount_vs_reference_pct"])
        self.assertIn("1% 미만 저렴", tiny["caption"])
        self.assertTrue(tiny["reference_discount_under_one_pct"])
        no = create_default_preview(11000, "소금 1개", 10000)
        self.assertIsNone(no["approx_discount_vs_reference_pct"])
        self.assertFalse(no["reference_discount_under_one_pct"])

    def test_verified_long_prefix_match_both_directions(self):
        self.assertTrue(same_named_option("Apple 아이맥 24 M4칩, 16GB, 256GB",
                                          "Apple 아이맥 24 M4칩"))
        self.assertFalse(same_named_option("Apple 아이맥 24 M4칩, 8GB",
                                           "Apple 아이맥 24 M4칩, 16GB"))
        self.assertTrue(same_named_option("짜왕, 8개", "짜왕, 8개"))

    def test_product_then_usual_price_then_hot_deal(self):
        caption = create_default_preview(12050, "휘카레 소스", 34575, 123)
        self.assertEqual(caption["caption"].split("\n\n"), [
            "[65%할인] 휘카레 소스",
            "💸 쿠팡 평소 판매가 : 약 34,575원",
            "🔥 핫딜 특가 : 12,050원 (약 65% 할인 / 상세페이지 참고)",
        ])
        self.assertEqual(caption["rounded_discount_step"], 5)
        self.assertFalse(caption["official_normal_price_quoted"])
        self.assertFalse(caption["auto_publish_ready"])

    def test_long_verified_name_is_identical_in_body_and_150_char_title(self):
        name = "로우차트 제품 " + "제품상세" * 35
        caption = create_default_preview(12050, name, 34575)
        displayed = caption["product_name"]
        self.assertTrue(displayed.startswith("로우차트 제품 "))
        self.assertLessEqual(len("[쿠팡🔥핫딜] " + displayed + " [65%할인]"), 150)
        self.assertEqual(caption["caption"].split("\n\n")[0],
                         "[65%할인] " + displayed)

    def test_missing_mean_leaves_price_not_fake_discount(self):
        preview = create_default_preview(12050, "휘카레 소스", None)
        self.assertEqual(preview["caption"],
                         "[할인율 확인 중] 휘카레 소스\n\n"
                         "💸 쿠팡 평소 판매가 : 확인 중\n\n"
                         "🔥 핫딜 특가 : 12,050원 (상세페이지 참고)")
        self.assertIsNone(preview["approx_discount_vs_historical_mean_pct"])

    def test_mean_below_sale_doesnt_claim_discount(self):
        preview = create_default_preview(12050, "휘카레 소스", 10000)
        self.assertNotIn("% 혜택", preview["caption"])
        self.assertIn("쿠팡 평소 판매가 : 약 10,000원", preview["caption"])

    def test_missing_sale_is_not_invented(self):
        preview = create_default_preview(None, "휘카레 소스", 34575)
        self.assertEqual(preview["caption"].split("\n\n"), [
            "[할인율 확인 중] 휘카레 소스",
            "💸 쿠팡 평소 판매가 : 약 34,575원",
            "🔥 핫딜 특가 : 확인 중",
        ])
        self.assertNotIn("% 혜택", preview["caption"])

    def test_reference_not_renamed_official_coupang_price(self):
        preview = create_default_preview(12050, "휘카레 소스", 34575)
        self.assertIn("lowchart_public_history", preview["observed_average_source"])
        self.assertNotIn("쿠팡 공식 정상가", preview["caption"])
        self.assertFalse(preview["official_normal_price_quoted"])

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

    def test_actual_imac_generic_og_title_accepts_exact_lowchart_sku(self):
        p, i = "8444033257", "24426437522"
        og = "Apple 아이맥 24 M4칩 - 일체형PC | 쿠팡"
        low_title = ("Apple 아이맥 24 M4칩, 핑크, M4 8코어, 8코어 GPU, "
                     "16GB, 256GB, 스탠다드 글래스 - 2,116,500원 - 로우차트")
        html = (f"<html><title>{low_title}</title><body>"
                f"상품번호 {p} 옵션 {i} 현재가 2,116,500원 관측 최저가 1,950,000원 "
                "관측 최고가 2,800,000원 관측 평균가 2,265,732원 "
                "최근 변동일 2026-10-31</body></html>")
        ref = parse_reference(html, build_url(p, i), p, i, ("Apple",), 1743000)
        self.assertTrue(ref["usable_historical_reference"])
        self.assertTrue(same_named_option(og, low_title))
        self.assertEqual(validated_history(ref, p, i, og), 2265732)
        out = create_default_preview(1743000, "Apple 아이맥 24 M4칩", 2265732)
        self.assertIn("약 2,265,732원", out["caption"])
        self.assertIn("[25%할인]", out["caption"])
        self.assertIn("1,743,000원 (약 25% 할인 / 상세페이지 참고)", out["caption"])

    def test_generic_og_title_cannot_override_wrong_exact_item(self):
        p, i = "8444033257", "24426437522"
        og = "Apple 아이맥 24 M4칩 - 일체형PC | 쿠팡"
        title = "Apple 아이맥 24 M4칩, 핑크, 16GB, 256GB - 로우차트"
        hist = {
            "usable_historical_reference": True,
            "official_normal_price": False,
            "source_item_identifiers_present": True,
            "title_option_matches": True,
            "product_id": p,
            "item_id": "24948353594",
            "source_url": build_url(p, i),
            "page_title": title,
            "observed_average_krw": 2265732,
        }
        self.assertIsNone(validated_history(hist, p, i, og))

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
