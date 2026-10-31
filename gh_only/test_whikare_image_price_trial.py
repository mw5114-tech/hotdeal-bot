"""Read-only guard tests for a second whikare hotdeal exact-item trial."""
import unittest
from gh_only.whikare_image_price_trial import (
    PRODUCT_ID, ITEM_ID, VENDOR_ITEM_ID, SOURCE_SHORT, SOURCE_PRODUCT,
    PRICE_NOW, ORIGINAL_STATED, STATED_SAVING, STATED_PERCENT
)
from gh_only.lowchart_price_reference import build_url
from gh_only.kakao_og_authenticated_probe import image_is_cdn

class WhikareOneShotTests(unittest.TestCase):
    def test_consistent_item_identity(self):
        self.assertEqual(PRODUCT_ID, "9217210892")
        self.assertEqual(ITEM_ID, "27232840915")
        self.assertEqual(VENDOR_ITEM_ID, "94199974724")
        self.assertIn(f"itemId={ITEM_ID}", SOURCE_PRODUCT)
        self.assertIn(f"vendorItemId={VENDOR_ITEM_ID}", SOURCE_PRODUCT)
        self.assertEqual(build_url(PRODUCT_ID, ITEM_ID),
                         "https://www.lowchart.com/9217210892-27232840915")

    def test_shortlink_is_provider_own(self):
        self.assertEqual(SOURCE_SHORT, "https://link.coupang.com/a/iiPBYpVHae")

    def test_telegram_price_arithmetic(self):
        self.assertEqual(ORIGINAL_STATED - PRICE_NOW, STATED_SAVING)
        self.assertEqual(round(100 * STATED_SAVING / ORIGINAL_STATED,1), STATED_PERCENT)

    def test_image_cdn_guard(self):
        self.assertTrue(image_is_cdn("https://thumbnail.coupangcdn.com/p.jpg"))
        self.assertFalse(image_is_cdn("https://thumbnail.coupangcdn.com.evil.test/p.jpg"))

if __name__ == "__main__":
    unittest.main()
