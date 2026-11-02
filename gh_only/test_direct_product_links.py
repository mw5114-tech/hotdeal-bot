"""Offline tests: GitHub uses only normal product URLs before Muse rewrites."""
import unittest
from gh_only.direct_product_links import regular_product_url, verify_regular_product_url


class DirectProductLinkTests(unittest.TestCase):
    def setUp(self):
        self.ids = {"status": "EXACT_ITEM_IDENTIFIED",
                    "product_id": "9440621860", "item_id": "28512619980"}

    def test_clean_url(self):
        url = regular_product_url(self.ids)
        self.assertEqual(url, "https://www.coupang.com/vp/products/9440621860?itemId=28512619980")
        self.assertTrue(verify_regular_product_url(url, self.ids))
        self.assertNotIn("lptag", url)
        self.assertNotIn("AFFSDP", url)

    def test_vendor_option(self):
        self.ids["vendor_item_id"] = "3034232401"
        url = regular_product_url(self.ids)
        self.assertEqual(url,
            "https://www.coupang.com/vp/products/9440621860?itemId=28512619980&vendorItemId=3034232401")
        self.assertTrue(verify_regular_product_url(url, self.ids))

    def test_reject_extra_tracking_and_wrong_option(self):
        url = regular_product_url(self.ids)
        for candidate in (url + "&lptag=AF9668915",
                          url + "&traceid=V0",
                          url.replace("28512619980", "28512619981"),
                          url.replace("www.coupang.com", "coupang.com"),
                          url.replace("https://", "http://"),
                          url + "#foo"):
            self.assertFalse(verify_regular_product_url(candidate, self.ids))

    def test_reject_unverified_input(self):
        with self.assertRaisesRegex(ValueError, "UNVERIFIED"):
            regular_product_url({**self.ids, "status": "UNKNOWN"})
        with self.assertRaisesRegex(ValueError, "IDS_INVALID"):
            regular_product_url({**self.ids, "item_id": "abc"})

if __name__ == "__main__":
    unittest.main()
