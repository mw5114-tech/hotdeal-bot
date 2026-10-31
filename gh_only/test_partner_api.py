"""Partner API offline contract tests; no actual Coupang API calls."""
import hashlib
import hmac
from datetime import datetime, timezone
import json
import unittest

from gh_only.partner_api import (
    DEEPLINK_PATH, canonical_product_url, create_deeplink,
    parse_deeplink_response, safe_link, sign,
)


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.data


class ApiContractTests(unittest.TestCase):
    def test_canonical_removes_other_affiliate_attribution(self):
        original = ("https://www.coupang.com/vp/products/9412434217"
                    "?itemId=25858815414&lptag=AF_SOMEBODY_ELSE&subid=telegram1")
        self.assertEqual(
            canonical_product_url(original),
            "https://www.coupang.com/vp/products/9412434217?itemId=25858815414",
        )

    def test_vendor_item_id_preserved(self):
        self.assertEqual(canonical_product_url(
            "https://www.coupang.com/vp/products/100?itemId=200&vendorItemId=300"),
            "https://www.coupang.com/vp/products/100?itemId=200&vendorItemId=300")

    def test_no_implied_option_when_missing(self):
        for url in ("https://www.coupang.com/vp/products/123",
                    "https://link.coupang.com/a/abc",
                    "https://evil.example/vp/products/123?itemId=456"):
            with self.assertRaises(ValueError):
                canonical_product_url(url)

    def test_sign_hmac(self):
        now = datetime(2026, 10, 31, 13, 0, 1, tzinfo=timezone.utc)
        header = sign("POST", DEEPLINK_PATH, "test-access", "test-secret", now)
        digest = hmac.new(b"test-secret",
                          ("261031T130001ZPOST" + DEEPLINK_PATH).encode(),
                          hashlib.sha256).hexdigest()
        self.assertIn("signed-date=261031T130001Z", header)
        self.assertTrue(header.endswith("signature=" + digest))

    def test_requires_approved_keys(self):
        with self.assertRaises(ValueError):
            sign("POST", DEEPLINK_PATH, "", "")

    def test_link_host_and_scheme_strict(self):
        self.assertTrue(safe_link("https://link.coupang.com/a/EXAMPLE"))
        self.assertTrue(safe_link("https://coupa.ng/bgQjht"))
        self.assertFalse(safe_link("http://link.coupang.com/a/EXAMPLE"))
        self.assertFalse(safe_link("https://evil.example/a/EXAMPLE"))

    def test_response_rejects_unrelated_url(self):
        data = json.dumps({"rCode": "0", "data": [
            {"originalUrl": "https://www.coupang.com/vp/products/1?itemId=2",
             "shortenUrl": "https://coupa.ng/test"}
        ]})
        with self.assertRaises(RuntimeError):
            parse_deeplink_response(data, "https://www.coupang.com/vp/products/100?itemId=200")

    def test_exact_response_can_be_parsed(self):
        target = "https://www.coupang.com/vp/products/100?itemId=200"
        data = json.dumps({"rCode": "0", "data": [
            {"originalUrl": target, "shortenUrl": "https://link.coupang.com/a/ABC123"}
        ]})
        self.assertEqual(parse_deeplink_response(data, target),
                         "https://link.coupang.com/a/ABC123")

    def test_request_payload_mock_only(self):
        def fake_open(req, timeout):
            self.assertEqual(timeout, 20)
            payload = json.loads(req.data)
            target = "https://www.coupang.com/vp/products/100?itemId=200"
            self.assertEqual(payload["coupangUrls"], [target])
            self.assertEqual(payload["subId"], "telegram1")
            return FakeResponse(json.dumps({"rCode": "0", "data": [
                {"originalUrl": target, "shortenUrl": "https://coupa.ng/abc123"}
            ]}).encode())

        result = create_deeplink(
            "https://www.coupang.com/vp/products/100?itemId=200&lptag=ANOTHER",
            "test-access", "test-secret", opener=fake_open)
        self.assertEqual(result["own_affiliate_url"], "https://coupa.ng/abc123")
        self.assertFalse(result["exact_option_rendered_verified"])


if __name__ == "__main__":
    unittest.main()
