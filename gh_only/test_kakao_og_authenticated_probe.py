"""Offline test guards: never print, connect, or export session values."""
import unittest
import json
from gh_only.kakao_og_authenticated_probe import (
    safe_session_state, provider_domain_allowed, image_is_cdn
)

class KakaoOGTests(unittest.TestCase):
    def test_no_provider_cookie_means_no_session(self):
        with self.assertRaisesRegex(ValueError, "NO_KAKAO_LOGIN_COOKIES"):
            safe_session_state(json.dumps({"cookies": [], "origins": []}))

    def test_kakao_and_daum_subdomains_kept(self):
        state = {"cookies": [{"domain": ".kakao.com"}, {"domain": "logins.daum.net"},
                             {"domain": "kauth.kakao.com"}], "origins": []}
        self.assertEqual(len(safe_session_state(json.dumps(state))["cookies"]), 3)

    def test_unrelated_cookies_removed_not_imported(self):
        state = {"cookies": [{"domain": ".kakao.com"}, {"domain": ".tracking.example"}],
                 "origins": []}
        scoped = safe_session_state(json.dumps(state))
        self.assertEqual([x["domain"] for x in scoped["cookies"]], [".kakao.com"])

    def test_unrelated_origins_removed_not_imported(self):
        state = {"cookies": [{"domain": ".kakao.com"}],
                 "origins": [{"origin": "https://evil.example", "localStorage": []},
                             {"origin": "https://developers.kakao.com", "localStorage": []}]}
        scoped = safe_session_state(json.dumps(state))
        self.assertEqual(len(scoped["origins"]), 1)
        self.assertEqual(scoped["origins"][0]["origin"], "https://developers.kakao.com")

    def test_no_unrelated_only_cookie(self):
        with self.assertRaisesRegex(ValueError, "NO_KAKAO_LOGIN_COOKIES"):
            safe_session_state(json.dumps({
                "cookies": [{"domain": ".evil.example"}], "origins": []
            }))

    def test_domain_lookalike_rejected(self):
        for candidate in ("kakao.com.evil.example", "notkakao.com",
                          "fake-daum.net", ".evil.example", None):
            self.assertFalse(provider_domain_allowed(candidate))
        self.assertTrue(provider_domain_allowed(".developers.kakao.com"))
        self.assertTrue(provider_domain_allowed("logins.daum.net"))

    def test_malformed_json_rejected(self):
        with self.assertRaises(ValueError):
            safe_session_state("not-json")

    def test_image_cdn_only(self):
        self.assertTrue(image_is_cdn(
            "https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/example.jpg"))
        self.assertFalse(image_is_cdn(
            "https://thumbnail.coupangcdn.com.evil.example/a.jpg"))
        self.assertFalse(image_is_cdn("http://thumbnail.coupangcdn.com/example.jpg"))
        self.assertFalse(image_is_cdn("https://evil.example/a.jpg"))

if __name__ == "__main__":
    unittest.main()
