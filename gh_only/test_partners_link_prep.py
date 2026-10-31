"""Zero-network safety tests for input passed to the user's own Partners UI."""
import unittest

from gh_only.partners_link_prep import prepare_url

PID, IID, VID = "9217210892", "27232840915", "94199974724"


class InputPrepTests(unittest.TestCase):
    def test_exact_shortlink_and_vendor_option_preserved(self):
        def hop(url):
            self.assertEqual(url, "https://link.coupang.com/a/iiPBYpVHae")
            return {"result": "DIRECT_PRODUCT_ID_CANDIDATE",
                    "product_id": PID, "item_id": IID, "vendor_item_id": VID}
        r = prepare_url("https://link.coupang.com/a/iiPBYpVHae", resolve=hop)
        self.assertEqual(r["status"], "INPUT_READY")
        self.assertEqual(r["canonical_url"],
            "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724")
        self.assertFalse(r["original_affiliate_link_reused"])

    def test_long_affiliate_url_removes_all_tracking(self):
        r = prepare_url(
            "https://www.coupang.com/vp/products/9217210892"
            "?itemId=27232840915&vendorItemId=94199974724&lptag=AF12345&src=1139000"
            "&subid=telegram1&traceid=123"
        )
        self.assertEqual(r["canonical_url"],
            "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724")
        self.assertNotIn("lptag",r["canonical_url"])
        self.assertNotIn("src=",r["canonical_url"])
        self.assertNotIn("subid",r["canonical_url"])

    def test_item_id_cannot_be_missing(self):
        r = prepare_url("https://www.coupang.com/vp/products/9217210892")
        self.assertEqual(r["status"], "PRODUCT_ITEM_ID_REQUIRED")

    def test_shortlink_must_resolve_one_hop(self):
        r = prepare_url("https://link.coupang.com/a/iiPBYpVHae",
                        resolve=lambda url: {"result": "ADDITIONAL_HOP_NEEDED"})
        self.assertEqual(r["status"], "UNRESOLVED_SHORTLINK")

    def test_cannot_use_other_merchant_or_phishing(self):
        for url in (
            "https://evil.example/vp/products/9217210892?itemId=27232840915",
            "https://link.coupang.com.evil.example/a/iiPBYpVHae",
            "http://link.coupang.com/a/iiPBYpVHae",
        ):
            r = prepare_url(url, resolve=lambda _: self.fail("should not resolve"))
            self.assertEqual(r["status"], "NOT_A_SUPPORTED_COUPANG_PRODUCT")

    def test_cannot_use_different_vendor_option(self):
        r = prepare_url("https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=other")
        self.assertEqual(r["status"], "INPUT_READY") # invalid vendor id is discarded by identify_url
        self.assertIsNone(r["vendor_item_id"])

    def test_cannot_reuse_link_without_affiliate_id_proof(self):
        r = prepare_url("https://www.coupang.com/vp/products/6221369038?itemId=12453073752")
        self.assertTrue(r["canonical_url"].startswith("https://www.coupang.com/"))
        self.assertFalse(r["personal_partner_link_generated"])
        self.assertFalse(r["publisher_id_copied"])


if __name__ == "__main__":
    unittest.main()
