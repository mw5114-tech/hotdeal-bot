"""Offline-only tests. No actual HTTP request or authentication."""
import unittest
from coupang_owner_waf_probe import classify, validate_owner_token, safe_headers


class OwnerWafProbeTests(unittest.TestCase):
    def test_http_200_access_denied_is_not_success(self):
        self.assertEqual(
            classify(200, "partners.coupang.com", "Access Denied", "<html>no</html>"),
            "DENIED_BY_SITE",
        )

    def test_http_403_not_success(self):
        self.assertEqual(classify(403, "partners.coupang.com", "", "blocked"), "DENIED_BY_SITE")

    def test_only_recognizable_partner_page_counts(self):
        self.assertEqual(classify(200, "partners.coupang.com", "쿠팡 파트너스", "hello"), "PUBLIC_PARTNERS_UI_REACHED")
        self.assertEqual(classify(200, "partners.coupang.com", "something", "hello"), "PUBLIC_RESPONSE_UNCONFIRMED")
        self.assertEqual(classify(200, "example.com", "Coupang Partners", "hello"), "HTTP_OR_DESTINATION_UNEXPECTED")

    def test_validate_test_token(self):
        good = "A" * 32
        self.assertEqual(validate_owner_token(good), good)
        self.assertEqual(validate_owner_token(""), "")
        for bad in ("unsafe", "x" * 300, "hello\\r\\nheader: evil", "bad space"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_owner_token(bad)

    def test_no_sensitive_response_headers(self):
        result = safe_headers({"Server": "edge", "CF-Ray": "abc123", "Set-Cookie": "secret",
                               "Authorization": "Bearer secret", "X-Request-ID": "req"})
        self.assertEqual(result, {"server": "edge", "cf-ray": "abc123", "x-request-id": "req"})


if __name__ == "__main__":
    unittest.main()
