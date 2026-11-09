"""Offline tests for truthful price labels, spacing and picture order."""
import unittest
from pathlib import Path
from gh_only.naver_blog_format import build_blocks, plain_body, validate_blocks
from gh_only.naver_blog_jirum import DISCLOSURE

URL = "https://link.coupang.com/re/AFFSDP?lptag=AF9668915&subid=naverblog&pageKey=123&itemId=456"

def row(source="lowchart_public_history", reference=19900, discount=25):
    draft = {
        "caption": {
            "product_name": "샘플 상품 10개",
            "sale_price_krw": 14530,
            "comparison_reference_price_krw": reference,
            "comparison_reference_source": source,
            "approx_discount_vs_reference_pct": discount,
        },
        "image_source": "lowchart_exact_item_public_page",
    }
    verified = {"candidate_url": URL, "image": Path("jirum_123_lowchart_photo.png")}
    source_row = {"source_post_url": "https://t.me/jirum/123"}
    return draft, verified, source_row

class FormattingTests(unittest.TestCase):
    def format(self, *a, **kw):
        blocks = build_blocks(*row(*a, **kw))
        validate_blocks(blocks, URL)
        return blocks, plain_body(blocks)

    def test_first_disclosure_second_hero_image(self):
        blocks, body = self.format()
        self.assertEqual(blocks[0]["text"], DISCLOSURE)
        self.assertEqual(blocks[1]["kind"], "hero_photo")
        self.assertEqual(body.count(URL), 0)
        self.assertNotIn("https://link.coupang.com/", body)
        self.assertEqual(body.count("📦 쿠팡에서 상품정보 더보기"), 1)
        self.assertEqual(blocks[-1]["link_url"], URL)
        self.assertTrue(body.startswith(DISCLOSURE + "\n\n"))
        self.assertGreaterEqual(body.count("\n\n"), 7)

    def test_lowchart_average_clearly_labeled(self):
        blocks, body = self.format()
        self.assertIn("쿠팡 평소 판매가(로우차트 관측 평균)", body)
        self.assertIn("14,530원", body)
        self.assertIn("19,900원", body)
        self.assertIn("[약 25% 할인]", body)
        self.assertTrue(any(x.get("bold_prefix") == "🔥 오늘 특가" for x in blocks))

    def test_user_requested_real_time_order_and_adjacent_notice(self):
        blocks, body = self.format()
        items = [x for x in blocks if x.get("type") == "text"]
        kinds = [x.get("kind") for x in items]
        self.assertIn("🛍️ 실시간 쿠팡 핫딜", body)
        self.assertNotIn("🛍️ 오늘의 쿠팡 핫딜", body)
        ref = kinds.index("reference")
        self.assertEqual(kinds[ref + 1], "price")
        self.assertEqual(kinds[ref + 2], "note")
        self.assertEqual(items[ref + 1]["text"],
                         "🔥 오늘 특가 : 약 14,530원 [약 25% 할인]")
        self.assertEqual(items[ref + 2]["text"],
                         "※ 실시간 핫딜 특성상 준비된 재고가 소진되면 가격이 원래대로 인상되거나 품절될 수 있습니다.")
        self.assertEqual(kinds.count("note"), 1)
        self.assertEqual(kinds.count("discount"), 0)

    def test_missing_comparison_never_invents_discount_badge(self):
        blocks, _ = self.format(None, reference=None, discount=65)
        sale = next(x["text"] for x in blocks if x.get("kind") == "price")
        self.assertNotIn("%", sale)
        self.assertIn("평소 판매가 : 확인되지 않음", plain_body(blocks))

    def test_fallcent_max_not_mislabeled_typical(self):
        _, body = self.format("fallcent_observed_high")
        self.assertIn("폴센트 관측 최고가", body)
        self.assertNotIn("쿠팡 평소 판매가", body)
        self.assertIn("가격 비교 기준 : 폴센트 관측 최고가", body)
        self.assertIn("[약 25% 할인]", body)

    def test_naver_search_is_comparison_not_average(self):
        _, body = self.format("naver_general_search_first_shopping_card")
        self.assertIn("네이버 쇼핑 검색 비교가", body)
        self.assertNotIn("쿠팡 평소 판매가", body)

    def test_unknown_reference_has_no_fabricated_percent(self):
        _, body = self.format(None, reference=None, discount=None)
        self.assertIn("평소 판매가 : 확인되지 않음", body)
        self.assertNotIn("약 25%", body)

    def test_user_requested_disclaimer_replaces_old_note_and_source(self):
        blocks, body = self.format()
        self.assertIn("※ 실시간 핫딜 특성상 준비된 재고가 소진되면 가격이 원래대로 인상되거나 품절될 수 있습니다.", body)
        self.assertNotIn("핫딜 정보는 텔레그램 게시 시점 기준입니다", body)
        self.assertNotIn("원문 출처", body)
        self.assertNotIn("https://t.me/jirum", body)
        self.assertEqual(blocks[-1]["kind"], "affiliate_link")

    def test_affiliate_url_is_only_a_hidden_native_link_target(self):
        blocks, body = self.format()
        self.assertEqual(blocks[-1]["kind"], "affiliate_link")
        self.assertEqual(blocks[-1]["text"], "📦 쿠팡에서 상품정보 더보기")
        self.assertEqual(blocks[-1]["link_url"], URL)
        self.assertEqual(body.count("📦 쿠팡에서 상품정보 더보기"), 1)
        self.assertNotIn("AFFSDP", body)
        self.assertNotIn("subid=naverblog", body)
        broken = [dict(x) for x in blocks]
        broken[-1]["link_url"] = URL.replace("subid=naverblog", "subid=daangncafe")
        with self.assertRaisesRegex(ValueError, "LINK_TARGET"):
            validate_blocks(broken, URL)

    def test_manual_test_notice(self):
        d,v,s = row()
        blocks = build_blocks(d,v,s,manual_test_replay=True)
        self.assertIn("서식 테스트", plain_body(blocks))

if __name__ == "__main__":
    unittest.main()
