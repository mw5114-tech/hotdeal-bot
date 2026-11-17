"""No-post regressions: fail-safe Lowchart 403 backoff and cross-run cache."""

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from gh_only.lowchart_price_reference import fetch_one
from gh_only.lowchart_product_photo import lookup_exact_item_photo
from gh_only.lowchart_shared_reference import (
    CACHE_BRANCH, CACHE_WRITE_RETRY_SECONDS, WAIT_SECONDS,
    _api, create_record, verified_history,
    read_record, write_record, maybe_share_or_recover,
)
from gh_only.test_lowchart_shared_reference import (
    real_history, NEW_NAME, NEW_PID, NEW_IID, SOURCE_ID,
)


class LowchartRetryAndCacheTests(unittest.TestCase):
    def test_price_403_then_200_stops_immediately(self):
        requests, waits = [], []
        def once(*args, **kwargs):
            requests.append(kwargs)
            if len(requests) < 3:
                return {"http_status": 403, "reason": "HTTP_ERROR"}
            return {"http_status": 200, "reason": "EXACT_ITEM_VISIBLE_RECENT_AVERAGE_MATCH",
                    "observed_average_krw": 15460}
        with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "true"}), \
             patch("gh_only.lowchart_price_reference._fetch_one_once", side_effect=once):
            o = fetch_one(NEW_PID, NEW_IID, (), 11350,
                          sleeper=waits.append)
        self.assertEqual(o["observed_average_krw"], 15460)
        self.assertEqual(len(requests), 3)
        self.assertEqual(waits, [3, 7])

    def test_price_persistent_403_stops_with_bounded_audit(self):
        waits = []
        with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "true"}), \
             patch("gh_only.lowchart_price_reference._fetch_one_once",
                   return_value={"http_status": 403, "reason": "HTTP_ERROR"}) as once:
            o = fetch_one(NEW_PID, NEW_IID, (), 11350,
                          sleeper=waits.append)
        self.assertEqual(once.call_count, 4)
        self.assertEqual(waits, [3, 7, 14])
        self.assertTrue(o["retry_exhausted"])
        self.assertEqual(o["http_403_attempts"], 4)

    def test_photo_403_then_200_stops_and_keeps_exact_html(self):
        bad = {"http_status": 403, "status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR"}
        good = {"status": "LOWCHART_EXACT_ITEM_IMAGE", "lowchart_title_verified": True,
                "_verified_public_item_html": "<title>real exact item</title>"}
        delays = []
        identity = {"status": "EXACT_ITEM_IDENTIFIED",
                    "product_id": NEW_PID, "item_id": NEW_IID}
        with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "true"}), \
             patch("gh_only.lowchart_product_photo._lookup_exact_item_photo_once",
                   side_effect=[bad,good]) as once:
            o = lookup_exact_item_photo(identity, NEW_NAME,
                                        sleeper=delays.append)
        self.assertEqual(once.call_count, 2)
        self.assertEqual(delays, [3])
        self.assertEqual(o["_verified_public_item_html"], good["_verified_public_item_html"])

    def test_photo_no_retry_on_other_status_or_on_disabled_policy(self):
        identity = {"status": "EXACT_ITEM_IDENTIFIED",
                    "product_id": NEW_PID, "item_id": NEW_IID}
        with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "true"}), \
             patch("gh_only.lowchart_product_photo._lookup_exact_item_photo_once",
                   return_value={"status": "LOWCHART_ITEM_PAGE_NOT_FOUND",
                                 "http_status": 404}) as once:
            o = lookup_exact_item_photo(identity, NEW_NAME)
        self.assertEqual(o["http_status"], 404)
        self.assertEqual(once.call_count, 1)
        with patch.dict(os.environ, {"JIRUM_LOWCHART_403_RETRY_ENABLED": "false"}), \
             patch("gh_only.lowchart_product_photo._lookup_exact_item_photo_once",
                   return_value={"status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                                 "http_status": 403}) as once:
            o = lookup_exact_item_photo(identity, NEW_NAME)
        self.assertEqual(once.call_count, 1)
        self.assertTrue(o["retry_exhausted"])

    def test_cache_read_uses_isolated_branch_not_main(self):
        import base64
        payload = json.dumps({"schema": "safe-smoke"}).encode()
        response = {"type": "file", "content": base64.b64encode(payload).decode()}
        called = []
        def fake_open(req, timeout):
            called.append(req.full_url)
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self, n): return json.dumps(response).encode()
            return Response()
        with patch("gh_only.lowchart_shared_reference.request.urlopen",
                   side_effect=fake_open):
            result = read_record(SOURCE_ID, "fake-token")
        self.assertEqual(result["schema"], "safe-smoke")
        self.assertEqual(len(called), 1)
        self.assertIn("ref="+CACHE_BRANCH, called[0])
        self.assertNotIn("ref=main", called[0])

    def test_cache_write_uses_dedicated_branch_and_retries_409(self):
        record = create_record(SOURCE_ID, real_history(), NEW_NAME)
        self.assertIsNotNone(record)
        calls, delays = [], []
        def fake_api(sid, token, method, payload=None):
            calls.append((sid,method,payload))
            if len(calls) == 1:
                raise HTTPError("https://api.github.com/", 409, "Conflict", None, None)
            return {"content": {"sha": "smoke"}}
        with patch("gh_only.lowchart_shared_reference._api",
                   side_effect=fake_api), \
             patch("gh_only.lowchart_shared_reference.read_record",
                   return_value=None), \
             patch("gh_only.lowchart_shared_reference.time.sleep",
                   side_effect=delays.append):
            self.assertTrue(write_record(SOURCE_ID, record, "fake-token"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][2]["branch"], CACHE_BRANCH)
        self.assertEqual(delays, [1])

    def test_cache_write_conflicting_same_source_never_overwrites(self):
        record = create_record(SOURCE_ID, real_history(), NEW_NAME)
        valid = create_record(SOURCE_ID, real_history(), NEW_NAME)
        self.assertIsNotNone(valid)
        with patch("gh_only.lowchart_shared_reference._api",
                   side_effect=HTTPError("", 422, "exists", None, None)) as api, \
             patch("gh_only.lowchart_shared_reference.read_record",
                   return_value=valid), \
             patch("gh_only.lowchart_shared_reference.time.sleep") as sleep:
            self.assertTrue(write_record(SOURCE_ID, record, "fake-token"))
        self.assertEqual(api.call_count, 1)
        sleep.assert_not_called()
        tampered = dict(valid)
        tampered["source_id"] = SOURCE_ID+1
        with patch("gh_only.lowchart_shared_reference._api",
                   side_effect=HTTPError("", 422, "exists", None, None)) as api, \
             patch("gh_only.lowchart_shared_reference.read_record",
                   return_value=tampered), \
             patch("gh_only.lowchart_shared_reference.time.sleep") as sleep:
            self.assertFalse(write_record(SOURCE_ID, record, "fake-token"))
        self.assertEqual(api.call_count, 1)
        sleep.assert_not_called()

    def test_cache_still_rejects_other_option_stale_data_and_false_history(self):
        record = create_record(SOURCE_ID, real_history(), NEW_NAME)
        self.assertIsNotNone(verified_history(record, SOURCE_ID, NEW_PID, NEW_IID))
        self.assertIsNone(verified_history(record, SOURCE_ID, NEW_PID, "7777777777"))
        bad = dict(record)
        bad["history"] = dict(record["history"])
        bad["history"]["observed_average_krw"] = 9999999
        self.assertIsNone(verified_history(bad, SOURCE_ID, NEW_PID, NEW_IID))
        result = maybe_share_or_recover(
            SOURCE_ID, NEW_PID, "7777777777",
            {"http_status": 403}, NEW_NAME, token="fake-token",
            reader=lambda *args: record, sleeper=lambda _: None,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
