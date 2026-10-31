"""Kakao OG UI session and image guard tests, no browser/network/account access."""
import unittest
import json
from gh_only.kakao_og_authenticated_probe import safe_session_state, image_is_cdn

class KakaoOGTests(unittest.TestCase):
    def test_valid_empty_session_structure(self):
        d = {"cookies": [], "origins": []}
        self.assertEqual(safe_session_state(json.dumps(d)), d)

    def test_only_kakao_domain_cookies(self):
        d = {"cookies": [{"domain": ".kakao.com"}], "origins": []}
        self.assertEqual(len(safe_session_state(json.dumps(d))["cookies"]), 1)

    def test_external_cookie_disallowed(self):
        with self.assertRaises(ValueError):
            safe_session_state(json.dumps({
                "cookies": [{"domain": ".evil.example"}], "origins": []
            }))

    def test_external_storage_origin_disallowed(self):
        with self.assertRaises(ValueError):
            safe_session_state(json.dumps({
                "cookies": [], "origins": [{"origin": "https://evil.example",
                                          "localStorage": []}]
            }))

    def test_image_cdn_only(self):
        self.assertTrue(image_is_cdn(
            "https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/example.jpg"))
        self.assertFalse(image_is_cdn(
            "https://thumbnail.coupangcdn.com.evil.example/a.jpg"))
        self.assertFalse(image_is_cdn("http://thumbnail.coupangcdn.com/example.jpg"))
        self.assertFalse(image_is_cdn("https://evil.example/a.jpg"))

if __name__ == "__main__":
    unittest.main()
