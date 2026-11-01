"""Jirum -> Daangn post-draft contracts; all inputs local, no login/posting."""
import unittest

from gh_only.daangn_draft import (
    AFFILIATE_DISCLOSURE, LINK_PENDING, build_bundle, build_draft,
    link_proof_is_valid, normalized_title, notes_from_source,
)
from gh_only.pipeline import price_info

SAMPLE = """🔥🔥🔥무지성 3,180원 (49.0% 할인)🔥🔥
오뚜기 핫케이크 믹스
🔥 선구매후고민, 🔥 선구매후고민
📌 가격 현황
- 정상가: 6,235원
- 할인: 3,055원
📦 특가 바로가기
https://link.coupang.com/a/iiCmQxWdUG
이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."""

HTML = """<html><body><div class="tgme_widget_message" data-post="jirum/94709">
<div class="tgme_widget_message_text">🔥🔥🔥무지성 3,180원 (49.0% 할인)<br>
오뚜기 핫케이크 믹스<br>🔥 선구매후고민<br>📌 가격 현황<br>
- 정상가: 6,235원<br>- 할인: 3,055원<br>
📦 특가 바로가기 <a href="https://link.coupang.com/a/iiCmQxWdUG">상품 링크</a><br>
이 포스팅은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.
</div></div></body></html>"""


class ScreenshotExampleTests(unittest.TestCase):
    def test_sale_is_3180_not_discount_amount_3055(self):
        p = price_info(SAMPLE)
        self.assertEqual(p["sale_price_krw"], 3180)
        self.assertEqual(p["original_price_krw"], 6235)
        self.assertEqual(p["discount_amount_krw"], 3055)
        self.assertEqual(p["discount_percent_computed"], 49.0)
        self.assertFalse(p["live_coupang_price_verified"])

    def test_conflicting_arithmetic_fails_closed(self):
        p = price_info(SAMPLE.replace("3,055원", "3,050원"))
        self.assertIsNone(p["sale_price_krw"])
        self.assertEqual(p["price_confidence"], "conflicting_discount_arithmetic")

    def test_conflicting_discount_percent_fails_closed(self):
        p = price_info(SAMPLE.replace("49.0%", "35.0%"))
        self.assertIsNone(p["sale_price_krw"])
        self.assertEqual(p["price_confidence"], "conflicting_discount_percentage")

    def test_correct_product_name_skips_price_headline(self):
        self.assertEqual(normalized_title(SAMPLE), "오뚜기 핫케이크 믹스")

    def test_dont_copy_hype_lines(self):
        self.assertNotIn("선구매후고민", "\n".join(notes_from_source(SAMPLE, "오뚜기 핫케이크 믹스")))


class DaangnDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle(HTML)

    def test_one_coupang_draft(self):
        self.assertEqual(self.bundle["source_total"], 1)
        self.assertEqual(self.bundle["coupang_draft_count"], 1)
        self.assertEqual(self.bundle["published_count"], 0)

    def test_factual_fields_and_correct_title(self):
        draft = self.bundle["drafts"][0]
        self.assertEqual(draft["title"], "오뚜기 핫케이크 믹스")
        self.assertEqual(draft["sale_price_krw"], 3180)
        self.assertEqual(draft["original_price_krw"], 6235)
        self.assertIn("49.0%", draft["article_body"])

    def test_others_affiliate_link_must_not_appear_in_article(self):
        draft = self.bundle["drafts"][0]
        self.assertNotIn("iiCmQxWdUG", draft["article_body"])
        self.assertNotIn("link.coupang.com", draft["article_body"])
        self.assertIn(LINK_PENDING, draft["article_body"])

    def test_disclosure_not_added_when_no_affiliate_link(self):
        draft = self.bundle["drafts"][0]
        self.assertNotIn(AFFILIATE_DISCLOSURE, draft["article_body"])
        self.assertIsNone(draft["own_affiliate_url"])
        self.assertFalse(draft["publish_ready"])

    def test_no_product_photo_claimed(self):
        draft = self.bundle["drafts"][0]
        self.assertIsNone(draft["photo_candidate"])
        self.assertIn("PRODUCT_IMAGE_MISSING", draft["blockers"])

    def test_external_merchant_is_excluded(self):
        html = HTML.replace("https://link.coupang.com/a/iiCmQxWdUG",
                            "https://www.other-store.example/deal/123")
        bundle = build_bundle(html)
        self.assertEqual(bundle["coupang_draft_count"], 0)

    def test_no_caller_supplied_shortlink_accepted_as_owned_proof(self):
        record = {
            "product_id": "9412434217", "item_id": "25858815414",
            "vendor_item_id": None,
        }
        self.assertFalse(link_proof_is_valid({"own_affiliate_url":
                         "https://link.coupang.com/a/SOMELINK"}, record))
        self.assertFalse(link_proof_is_valid({
            "method": "approved_partners_api_verified",
            "own_account_confirmed": True, "item_identity_confirmed": False,
            "product_id": "9412434217", "item_id": "25858815414",
            "own_affiliate_url": "https://link.coupang.com/a/SOMELINK"
        }, record))

    def test_mock_identity_verified_link_still_does_not_autopost(self):
        record = {
            "source_message_id": 12, "source_post_url": "https://t.me/jirum/12",
            "source_text": SAMPLE, "url_kind": "coupang_product",
            "product_id": "9412434217", "item_id": "25858815414",
            "vendor_item_id": None, "image_provenance": "photo_not_visible",
            "image_url_candidate": None, "exact_option_verified": False,
            **price_info(SAMPLE)
        }
        proof = {
            "method": "approved_partners_api_verified",
            "own_account_confirmed": True, "item_identity_confirmed": True,
            "product_id": "9412434217", "item_id": "25858815414",
            "own_affiliate_url": "https://link.coupang.com/a/OWNEXAMPLE"
        }
        result = build_draft(record, proof)
        self.assertEqual(result["own_affiliate_url"], proof["own_affiliate_url"])
        self.assertIn(AFFILIATE_DISCLOSURE, result["article_body"])
        self.assertFalse(result["publish_ready"])
        self.assertEqual(result["status"], "DRAFT_ONLY_DO_NOT_PUBLISH")


if __name__ == "__main__":
    unittest.main()
