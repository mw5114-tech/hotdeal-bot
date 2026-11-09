"""Offline safety checks for guarded Naver Blog one-shot writer."""
import json
import unittest

from naver_blog_one_shot import (
    TEST_TITLE, TEST_BODY, publication_log_number, safely_parse_state,
)


class OneShotTests(unittest.TestCase):
    def test_missing_session_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "AUTH_SESSION_MISSING"):
            safely_parse_state("")

    def test_invalid_session_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "AUTH_SESSION_INVALID_JSON"):
            safely_parse_state("{not-json")

    def test_non_naver_cookies_rejected(self):
        with self.assertRaisesRegex(ValueError, "AUTH_SESSION_NO_NAVER_COOKIES"):
            safely_parse_state(json.dumps({"cookies": [{"name": "sid", "value": "x", "domain": "example.com"}], "origins": []}))

    def test_state_filters_non_naver_auth(self):
        data = safely_parse_state(json.dumps({
            "cookies": [
                {"name": "A", "value": "secret", "domain": ".naver.com", "path": "/"},
                {"name": "B", "value": "irrelevant", "domain": "example.com", "path": "/"},
            ],
            "origins": [
                {"origin": "https://blog.naver.com", "localStorage": []},
                {"origin": "https://example.com", "localStorage": []},
            ],
        }))
        self.assertEqual(len(data["cookies"]), 1)
        self.assertEqual(len(data["origins"]), 1)
        self.assertNotIn("irrelevant", str(data))

    def test_post_url_only_from_own_blog(self):
        self.assertEqual(
            publication_log_number("https://blog.naver.com/PostView.naver?blogId=mw5114&logNo=1234567890"),
            "1234567890",
        )
        self.assertEqual(publication_log_number("https://blog.naver.com/mw5114/1234567890"), "1234567890")
        self.assertIsNone(publication_log_number("https://blog.naver.com/someone/1234567890"))
        self.assertIsNone(publication_log_number("https://evil.example/mw5114/1234567890"))

    def test_test_post_not_advertisement(self):
        self.assertIn("자동화 테스트", TEST_TITLE)
        self.assertIn("테스트", TEST_BODY)
        self.assertNotIn("link.coupang.com", TEST_BODY)


if __name__ == "__main__":
    unittest.main()
