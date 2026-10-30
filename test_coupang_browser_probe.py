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


    def test_accepts_source_affiliate_shortlink(self):
        self.assertEqual(
            probe.validate_source_url("https://link.coupang.com/a/ihyVQ9K1qC"),
            "https://link.coupang.com/a/ihyVQ9K1qC",
        )

    def test_source_affiliate_validation_does_not_allow_fake_hosts(self):
        for url in (
            "https://link.coupang.com.evil.com/a/abc",
            "https://www.coupang.com/vp/products/123",
            "http://link.coupang.com/a/abc",
            "https://link.coupang.com/redirect/abc",
            "https://link.coupang.com/a/abc#x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.validate_source_url(url)

    def test_resolved_product_canonicalizes_and_removes_old_partner_tracking(self):
        url = (
            "https://www.coupang.com/vp/products/1234567"
            "?itemId=333&vendorItemId=444&lptag=AF0000000&subid=OTHERPARTNER"
            "&traceId=whatever"
        )
        self.assertEqual(
            probe.canonical_direct_product_url(url),
            "https://www.coupang.com/vp/products/1234567?itemId=333&vendorItemId=444",
        )

    def test_resolved_mobile_product_uses_same_exact_ids(self):
        self.assertEqual(
            probe.canonical_direct_product_url(
                "https://m.coupang.com/vm/products/1234567?vendorItemId=444&itemId=333"
            ),
            "https://www.coupang.com/vp/products/1234567?itemId=333&vendorItemId=444",
        )

    def test_refuses_uncertain_products_or_redirect_destinations(self):
        candidates = (
            "https://www.coupang.com/vp/products/1234567?itemId=333",
            "https://www.coupang.com/vp/products/1234567?vendorItemId=444",
            "https://www.coupang.com/np/search?q=cheddar",
            "https://partners.coupang.com/",
            "https://evil-coupang.com/vp/products/1234567?itemId=333&vendorItemId=444",
            "http://www.coupang.com/vp/products/1234567?itemId=333&vendorItemId=444",
        )
        for url in candidates:
            with self.subTest(url=url), self.assertRaises(ValueError):
                probe.canonical_direct_product_url(url)

    def test_one_browser_navigation_only_and_no_auth_profile_shared(self):
        class FakePage:
            url = "https://www.coupang.com/vp/products/7?itemId=8&vendorItemId=9&lptag=OTHER"
            def __init__(self):
                self.goto_calls = []
            def goto(self, url, **kwargs):
                self.goto_calls.append((url, kwargs))
            def wait_for_timeout(self, ms):
                self.wait_ms = ms
        class FakeContext:
            def __init__(self):
                self.page = FakePage()
                self.closed = False
            def new_page(self):
                return self.page
            def close(self):
                self.closed = True
        class FakeBrowser:
            def __init__(self):
                self.context = FakeContext()
                self.closed = False
            def new_context(self):
                return self.context
            def close(self):
                self.closed = True
        class FakePlaywright:
            def __init__(self):
                self.browser = FakeBrowser()
                self.chromium = self
            def launch(self, **kwargs):
                self.launch_kwargs = kwargs
                return self.browser

        fake = FakePlaywright()
        url = "https://link.coupang.com/a/ihyVQ9K1qC"
        self.assertEqual(
            probe.resolve_shortlink_once(fake, url),
            "https://www.coupang.com/vp/products/7?itemId=8&vendorItemId=9",
        )
        self.assertEqual(len(fake.browser.context.page.goto_calls), 1)
        self.assertEqual(fake.browser.context.page.goto_calls[0][0], url)
        self.assertTrue(fake.browser.closed)
        self.assertTrue(fake.browser.context.closed)

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
