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
        self.assertEqual(body.count(URL), 1)
        self.assertTrue(body.startswith(DISCLOSURE + "\n\n"))
        self.assertGreaterEqual(body.count("\n\n"), 8)

    def test_lowchart_average_clearly_labeled(self):
        blocks, body = self.format()
        self.assertIn("쿠팡 평소 판매가(로우차트 관측 평균)", body)
        self.assertIn("14,530원", body)
        self.assertIn("19,900원", body)
        self.assertIn("약 25%", body)
        self.assertTrue(any(x.get("bold_prefix") == "🔥 오늘 특가" for x in blocks))

    def test_fallcent_max_not_mislabeled_typical(self):
        _, body = self.format("fallcent_observed_high")
        self.assertIn("폴센트 관측 최고가", body)
        self.assertNotIn("쿠팡 평소 판매가", body)
        self.assertIn("폴센트 최고가 대비", body)

    def test_naver_search_is_comparison_not_average(self):
        _, body = self.format("naver_general_search_first_shopping_card")
        self.assertIn("네이버 쇼핑 검색 비교가", body)
        self.assertNotIn("쿠팡 평소 판매가", body)

    def test_unknown_reference_has_no_fabricated_percent(self):
        _, body = self.format(None, reference=None, discount=None)
        self.assertIn("평소 판매가 : 확인되지 않음", body)
        self.assertNotIn("약 25%", body)

    def test_manual_test_notice(self):
        d,v,s = row()
        blocks = build_blocks(d,v,s,manual_test_replay=True)
        self.assertIn("서식 테스트", plain_body(blocks))

if __name__ == "__main__":
    unittest.main()
