"""Offline parsing and candidate-format tests; no HTTP, cookies or affiliate posting."""
import unittest

from coupang_fresh_link_research import (
    extract_product_identity,
    hypothetical_landing_url,
    product_url,
)


class FreshLinkResearchTests(unittest.TestCase):
    def test_parses_direct_product_without_tracking_query(self):
        x = extract_product_identity(
            "https://www.coupang.com/vp/products/12345?"
            "itemId=23456&vendorItemId=34567&lptag=AF9876543&traceid=old"
        )
        self.assertEqual(x, {"productId": "12345", "itemId": "23456", "vendorItemId": "34567"})
        self.assertEqual(product_url(x), "https://www.coupang.com/vp/products/12345?itemId=23456&vendorItemId=34567")

    def test_parses_real_form_of_long_affiliate_landing(self):
        identity = extract_product_identity(
            "https://link.coupang.com/re/AFFSDP?"
            "lptag=AF1111111&pageKey=12345&itemId=23456&vendorItemId=34567"
            "&traceid=V0-153-example"
        )
        self.assertEqual(identity["productId"], "12345")
        self.assertEqual(identity["vendorItemId"], "34567")

    def test_cannot_reuse_other_person_tag(self):
        identity = extract_product_identity(
            "https://link.coupang.com/re/AFFSDP?"
            "lptag=AF1111111&pageKey=12345&itemId=23456&vendorItemId=34567"
            "&traceid=others"
        )
        candidate = hypothetical_landing_url(identity, "AF9999999")
        self.assertIn("lptag=AF9999999", candidate)
        self.assertNotIn("AF1111111", candidate)
        self.assertNotIn("traceid=", candidate)

    def test_can_parse_mobile_product_page(self):
        x = extract_product_identity(
            "https://m.coupang.com/vm/products/12?itemId=34&vendorItemId=56"
        )
        self.assertEqual(x["itemId"], "34")

    def test_opaque_short_links_must_first_resolve(self):
        with self.assertRaisesRegex(ValueError, "SHORTLINK_REQUIRES_OFFICIAL_REDIRECT"):
            extract_product_identity("https://link.coupang.com/a/ihyVQ9K1qC")

    def test_missing_option_must_not_guess(self):
        for query in ("itemId=34", "vendorItemId=56", "itemId=34&vendorItemId=0",
                      "itemId=34&vendorItemId=56&vendorItemId=57"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                extract_product_identity("https://www.coupang.com/vp/products/12?" + query)

    def test_untrusted_hosts_and_paths_fail(self):
        for url in (
            "https://link.coupang.com.evil.test/re/AFFSDP?pageKey=1&itemId=2&vendorItemId=3",
            "https://www.coupang.com@fake.example/vp/products/1?itemId=2&vendorItemId=3",
            "https://www.coupang.com/vp/search?itemId=2&vendorItemId=3",
            "http://www.coupang.com/vp/products/1?itemId=2&vendorItemId=3",
            "https://www.coupang.com/vp/products/1?itemId=2&vendorItemId=3#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                extract_product_identity(url)

    def test_ambiguous_product_id_rejected(self):
        with self.assertRaises(ValueError):
            extract_product_identity(
                "https://link.coupang.com/re/AFFSDP?pageKey=1&pageKey=2&itemId=3&vendorItemId=4"
            )

    def test_invalid_owner_tag_rejected(self):
        identity = {"productId":"12","itemId":"34","vendorItemId":"56"}
        for tag in ("AF", "AFabc", "AF111&subid=evil", "AF111\n", ""):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                hypothetical_landing_url(identity, tag)


if __name__ == "__main__":
    unittest.main()
