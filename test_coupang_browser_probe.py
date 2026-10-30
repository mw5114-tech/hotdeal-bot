"""Offline tests for the LOCAL-only supervised Coupang Partners smoke test."""
import unittest

import coupang_browser_probe as probe

GOOD = "https://www.coupang.com/vp/products/930936790?itemId=14017346930&vendorItemId=123456789"
PARTNER = "https://link.coupang.com/a/ihDInn0L5o"


class CoupangBrowserProbeTests(unittest.TestCase):
    def test_exact_option_product_url_accepted(self):
        self.assertEqual(probe.validate_product_url(GOOD), GOOD)

    def test_no_product_shortlink_or_wrong_host_accepted(self):
        for candidate in (
            PARTNER,
            "https://www.coupang.com/",
            "https://evil-coupang.com/vp/products/123?itemId=1&vendorItemId=2",
            "http://www.coupang.com/vp/products/123?itemId=1&vendorItemId=2",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                probe.validate_product_url(candidate)

    def test_requires_exact_item_and_vendor_ids(self):
        for candidate in (
            "https://www.coupang.com/vp/products/123",
            "https://www.coupang.com/vp/products/123?itemId=1",
            "https://www.coupang.com/vp/products/123?vendorItemId=2",
            "https://www.coupang.com/vp/products/123?itemId=no&vendorItemId=2",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                probe.validate_product_url(candidate)

    def test_good_personal_affiliate_short_link(self):
        self.assertEqual(probe.validate_personal_link(PARTNER), PARTNER)

    def test_disallow_nonaffiliate_or_unexpected_short_link(self):
        for candidate in (
            "https://link.coupang.com/other/ihDInn0L5o",
            "http://link.coupang.com/a/abc",
            "https://link.coupang.com.evil.example/a/abc",
            "https://link.coupang.com/a/abc?invalid=1",
            "https://link.coupang.com/a/abc#fragment",
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                probe.validate_personal_link(candidate)


if __name__ == "__main__":
    unittest.main()
