"""Offline, deterministic tests: no network and no publishing."""
import unittest
from pathlib import Path

from gh_only.pipeline import enrich_posts, identify_url, image_url, price_info

FIXTURE = Path(__file__).parent / "fixtures" / "jirum_sample.html"


class PriceTests(unittest.TestCase):
    def test_labeled_sale_and_original(self):
        p = price_info("젤리\n정상가 18,900원\n특가 14,100원")
        self.assertEqual(p["sale_price_krw"], 14100)
        self.assertEqual(p["original_price_krw"], 18900)
        self.assertAlmostEqual(p["discount_percent_computed"], 25.4, places=1)
        self.assertEqual(p["price_provenance"], "telegram_source_post")

    def test_shipping_not_sale(self):
        p = price_info("라면\n배송비 3,000원\n판매가 9,900원")
        self.assertEqual(p["sale_price_krw"], 9900)

    def test_conflicting_prices_fail_closed(self):
        p = price_info("상품\n판매가 11,900원\n판매가 12,900원")
        self.assertIsNone(p["sale_price_krw"])
        self.assertEqual(p["price_confidence"], "conflicting_telegram_prices")

    def test_original_more_than_sale_required(self):
        p = price_info("정상가 8,000원\n특가 9,000원")
        self.assertIsNone(p["original_price_krw"])

    def test_no_guess_from_discount_percentage(self):
        p = price_info("80% 할인!!")
        self.assertIsNone(p["sale_price_krw"])
        self.assertIsNone(p["original_price_krw"])

    def test_unlabeled_only_one_amount(self):
        p = price_info("상품\n19,900원")
        self.assertEqual(p["sale_price_krw"], 19900)
        self.assertEqual(p["price_confidence"], "unlabeled_telegram_price")

    def test_no_price_from_shipping_alone(self):
        self.assertIsNone(price_info("배송비 3,000원")["sale_price_krw"])


class UrlTests(unittest.TestCase):
    def test_shortlink_does_not_claim_option(self):
        link = identify_url("https://link.coupang.com/a/ABC123")
        self.assertEqual(link["url_kind"], "coupang_shortlink")
        self.assertIsNone(link["product_id"])
        self.assertFalse(link["exact_option_verified"])

    def test_direct_link_records_but_does_not_verify(self):
        link = identify_url("https://www.coupang.com/vp/products/9412434217?itemId=25858815414")
        self.assertEqual(link["product_id"], "9412434217")
        self.assertEqual(link["item_id"], "25858815414")
        self.assertFalse(link["exact_option_verified"])

    def test_image_protocol_restriction(self):
        self.assertIsNone(image_url("javascript:alert(1)"))
        self.assertIsNone(image_url("http://example.com/p.jpg"))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deals = enrich_posts(FIXTURE.read_text(encoding="utf-8"))

    def test_counts_and_ids(self):
        self.assertEqual([d["source_message_id"] for d in self.deals], [101, 102, 103])

    def test_one_telegram_photo(self):
        d = self.deals[0]
        self.assertEqual(d["image_url_candidate"], "https://cdn4.telesco.pe/file/sample-101.jpg")
        self.assertEqual(d["image_provenance"], "telegram_attached_photo")
        self.assertFalse(d["image_verified_for_exact_option"])

    def test_link_preview_is_not_accepted_as_product_photo(self):
        d = self.deals[1]
        self.assertIsNone(d["image_url_candidate"])
        self.assertEqual(d["image_provenance"], "photo_not_visible")

    def test_ambiguous_photo_blocks(self):
        d = self.deals[2]
        self.assertIsNone(d["image_url_candidate"])
        self.assertEqual(d["image_provenance"], "ambiguous_telegram_photos")

    def test_not_published_or_minted(self):
        for d in self.deals:
            self.assertIsNone(d["own_affiliate_url"])
            self.assertFalse(d["auto_publish_ready"])
            self.assertIn("OWN_AFFILIATE_LINK_NOT_CREATED", d["blockers"])

    def test_sale_and_shipping_separation(self):
        self.assertEqual(self.deals[1]["sale_price_krw"], 9900)
        self.assertIsNone(self.deals[1]["original_price_krw"])

    def test_price_conflict_not_silent(self):
        self.assertIsNone(self.deals[2]["sale_price_krw"])
        self.assertIn("PRICE_UNAVAILABLE", self.deals[2]["blockers"])

    def test_missing_products_fail(self):
        with self.assertRaises(RuntimeError):
            enrich_posts("<html>empty</html>")


if __name__ == "__main__":
    unittest.main()
