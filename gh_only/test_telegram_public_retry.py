"""Offline tests for bounded Telegram public HTML retries in the live FIFO scan.

No real Telegram, GitHub, Coupang or Daangn requests; no publishing.
"""
from __future__ import annotations

from io import BytesIO
from urllib import error
import unittest
from unittest.mock import patch

from gh_only.pipeline import (
    fetch_public_html, read_public_telegram_html, TELEGRAM_READ_ATTEMPTS,
)
from gh_only.jirum_manual_queue import older_html, scan
from gh_only.test_jirum_manual_queue import FakeStore, baseline

URL = "https://t.me/s/jirum"
BODY = "<html><body>Telegram latest public page</body></html>"


class FakeResponse:
    def __init__(self, body=BODY, url=URL):
        self.body = body.encode("utf-8")
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, n):
        return self.body[:n]


def http_error(status):
    return error.HTTPError(URL, status, "Temporary gateway failure", {}, None)


class TelegramReadRetryTests(unittest.TestCase):
    def read(self, opener, sleeper):
        return read_public_telegram_html(
            URL, redirect_error="PUBLIC_CHANNEL_REDIRECT",
            too_large_error="PUBLIC_CHANNEL_TOO_LARGE",
            opener=opener, sleeper=sleeper)

    def test_http_502_then_200_succeeds_without_skipping_any_page(self):
        calls, sleeps = [], []
        responses = [http_error(502), FakeResponse()]
        def opener(req, timeout):
            calls.append((req.full_url, timeout))
            row = responses.pop(0)
            if isinstance(row, Exception):
                raise row
            return row
        self.assertEqual(self.read(opener, sleeps.append), BODY)
        self.assertEqual(calls, [(URL, 25), (URL, 25)])
        self.assertEqual(sleeps, [1])

    def test_503_504_429_retry_with_strict_backoff(self):
        calls, sleeps = [], []
        results = [http_error(503), http_error(429), FakeResponse()]
        def opener(req, timeout):
            calls.append(req.full_url)
            obj = results.pop(0)
            if isinstance(obj, Exception): raise obj
            return obj
        self.assertEqual(self.read(opener, sleeps.append), BODY)
        self.assertEqual(calls, [URL, URL, URL])
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(TELEGRAM_READ_ATTEMPTS, 3)

    def test_transient_outage_exhausts_and_raises_without_false_success(self):
        calls, sleeps = [], []
        def opener(req, timeout):
            calls.append(req.full_url)
            raise http_error(502)
        with self.assertRaises(error.HTTPError) as err:
            self.read(opener, sleeps.append)
        self.assertEqual(err.exception.code, 502)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_403_is_permanent_and_never_retried(self):
        calls, sleeps = [], []
        def opener(req, timeout):
            calls.append(req.full_url)
            raise http_error(403)
        with self.assertRaises(error.HTTPError) as err:
            self.read(opener, sleeps.append)
        self.assertEqual(err.exception.code, 403)
        self.assertEqual(calls, [URL])
        self.assertEqual(sleeps, [])

    def test_unreachable_website_retries_but_does_not_fake_html(self):
        calls, sleeps = [], []
        def opener(req, timeout):
            calls.append(1)
            raise error.URLError("temporary network")
        with self.assertRaises(error.URLError):
            self.read(opener, sleeps.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_bad_redirect_is_never_retried(self):
        calls, sleeps = [], []
        def opener(req, timeout):
            calls.append(1)
            return FakeResponse(url="https://example.org/s/jirum")
        with self.assertRaisesRegex(RuntimeError, "PUBLIC_CHANNEL_REDIRECT"):
            self.read(opener, sleeps.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_invalid_source_url_blocked_without_network(self):
        def opener(*args, **kwargs):
            self.fail("network must never happen")
        for target in ("http://t.me/s/jirum",
                       "https://t.me.evil.com/s/jirum",
                       "https://t.me/s/private",
                       "https://name:pass@t.me/s/jirum"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    read_public_telegram_html(target,
                        redirect_error="PUBLIC_CHANNEL_REDIRECT",
                        too_large_error="PUBLIC_CHANNEL_TOO_LARGE",
                        opener=opener, sleeper=lambda _: None)

    def test_oversized_response_fails_closed_without_retry(self):
        calls, sleeps = [], []
        def opener(req, timeout):
            calls.append(1)
            return FakeResponse(body="x" * 2_000_001)
        with self.assertRaisesRegex(RuntimeError, "PUBLIC_CHANNEL_TOO_LARGE"):
            self.read(opener, sleeps.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_actual_latest_wrapper_uses_common_retry(self):
        responses = [http_error(502), FakeResponse()]
        def opener(req, timeout):
            obj = responses.pop(0)
            if isinstance(obj, Exception): raise obj
            return obj
        with patch("gh_only.pipeline.request.urlopen", side_effect=opener):
            with patch("gh_only.pipeline.time.sleep") as sleeper:
                self.assertEqual(fetch_public_html(), BODY)
        sleeper.assert_called_once_with(1)

    def test_history_wrapper_retries_and_keeps_before_cursor(self):
        before = 95403
        url = f"{URL}?before={before}"
        responses = [http_error(502), FakeResponse(url=url)]
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            obj = responses.pop(0)
            if isinstance(obj, Exception): raise obj
            return obj
        with patch("gh_only.pipeline.request.urlopen", side_effect=opener):
            with patch("gh_only.pipeline.time.sleep") as sleeper:
                self.assertEqual(older_html(before), BODY)
        self.assertEqual(calls, [url, url])
        sleeper.assert_called_once_with(1)

    def test_exhausted_source_does_not_update_durable_fifo_cursor(self):
        store = FakeStore(baseline())
        with patch("gh_only.jirum_manual_queue.collect_since",
                   side_effect=http_error(502)):
            with self.assertRaises(error.HTTPError):
                scan(store)
        self.assertEqual(store.value["last_scanned_id"],
                         baseline()["last_scanned_id"])
        self.assertEqual(store.value["pending"], [])
        self.assertIsNone(store.value["in_flight"])
        self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
