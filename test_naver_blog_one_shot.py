"""Offline safety checks for guarded Naver Blog one-shot writer."""
import json
import unittest
from unittest.mock import patch

from naver_blog_one_shot import (
    TEST_TITLE, TEST_BODY, publication_log_number, safely_parse_state,
    wait_for_publication_receipt,
)


class FakeContext:
    def __init__(self):
        self.pages = []
        self.clock = 0.0


class FakePage:
    """Offline Playwright-shaped page; no browser, network or publishing."""
    def __init__(self, context, url, on_wait=None):
        self.context = context
        self.url = url
        self.on_wait = on_wait
        self.frames = []
        self.polls = 0
        context.pages.append(self)

    def is_closed(self):
        return False

    def wait_for_timeout(self, delay_ms):
        self.context.clock += delay_ms / 1000
        self.polls += 1
        if self.on_wait:
            self.on_wait(self)


def poll_receipt(page, timeout_ms=1500):
    with patch("naver_blog_one_shot.monotonic",
               side_effect=lambda: page.context.clock):
        return wait_for_publication_receipt(
            page, timeout_ms=timeout_ms, interval_ms=100
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
        self.assertIsNone(publication_log_number(
            "https://blog.naver.com/PostView.naver?blogId=someone&logNo=1234567890"))
        self.assertIsNone(publication_log_number(
            "https://blog.naver.com/PostView.naver?logNo=1234567890"))
        self.assertIsNone(publication_log_number(
            "http://blog.naver.com/mw5114/1234567890"))
        self.assertIsNone(publication_log_number(
            "https://blog.naver.com/random?blogId=mw5114&logNo=1234567890"))
        self.assertIsNone(publication_log_number(
            "https://blog.naver.com/mw5114/1234567890/extra"))
        self.assertEqual(publication_log_number(
            "https://m.blog.naver.com/mw5114/1234567890"), "1234567890")

    def test_receipt_immediate_own_blog_url(self):
        ctx = FakeContext()
        page = FakePage(ctx, "https://blog.naver.com/mw5114/224472999999")
        self.assertEqual(poll_receipt(page), "224472999999")
        self.assertEqual(page.polls, 0)

    def test_receipt_delayed_navigation_after_old_2_5_second_limit(self):
        ctx = FakeContext()
        def redirect(page):
            if page.polls >= 31:
                page.url = "https://blog.naver.com/mw5114/224472999999"
        page = FakePage(ctx, "https://blog.naver.com/PostWriteForm.naver",
                        on_wait=redirect)
        self.assertEqual(poll_receipt(page, timeout_ms=5500),
                         "224472999999")
        self.assertGreater(ctx.clock, 2.5)

    def test_receipt_from_new_popup_tab(self):
        ctx = FakeContext()
        def spawn(page):
            if page.polls == 3:
                FakePage(ctx, "https://blog.naver.com/PostView.naver"
                              "?blogId=mw5114&logNo=224472999999")
        page = FakePage(ctx, "https://blog.naver.com/PostWriteForm.naver",
                        on_wait=spawn)
        self.assertEqual(poll_receipt(page), "224472999999")

    def test_foreign_blog_never_confirms_or_reposts(self):
        ctx = FakeContext()
        page = FakePage(ctx, "https://blog.naver.com/PostView.naver"
                        "?blogId=someone&logNo=224472999999")
        with self.assertRaisesRegex(RuntimeError,
                                    "SUBMISSION_UNCERTAIN_DO_NOT_RETRY"):
            poll_receipt(page)
        # Deadline rounding may end the final poll a millisecond early.
        self.assertGreaterEqual(ctx.clock, 1.4)

    def test_missing_or_ambiguous_receipts_never_confirm(self):
        for urls in (
            ("https://blog.naver.com/PostWriteForm.naver",),
            ("https://blog.naver.com/mw5114/224472111111",
             "https://blog.naver.com/mw5114/224472222222"),
        ):
            with self.subTest(urls=urls):
                ctx = FakeContext()
                pages = [FakePage(ctx, url) for url in urls]
                with self.assertRaisesRegex(
                        RuntimeError, "SUBMISSION_UNCERTAIN_DO_NOT_RETRY"):
                    poll_receipt(pages[0])

    def test_test_post_not_advertisement(self):
        self.assertIn("자동화 테스트", TEST_TITLE)
        self.assertIn("테스트", TEST_BODY)
        self.assertNotIn("link.coupang.com", TEST_BODY)


if __name__ == "__main__":
    unittest.main()
