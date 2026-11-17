"""Offline tests: only exact-item real historical means may cross channels."""
import copy
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from gh_only.lowchart_price_reference import parse_reference, build_url
from gh_only.lowchart_shared_reference import (
    SCHEMA, create_record, verified_history, maybe_share_or_recover,
    WAIT_SECONDS,
)
from gh_only.test_lowchart_price_reference import NEW_BODY, NEW_PID, NEW_IID, NEW_NAME
from gh_only.test_default_deal_automation import (
    DEAL, IDENTITY, PID, IID, HIST_HTML, H_TITLE,
)
from gh_only.default_deal_automation import enrich_deal

SOURCE_ID = 96120


def real_history():
    h = parse_reference(
        NEW_BODY, build_url(NEW_PID, NEW_IID), NEW_PID, NEW_IID,
        ("토종마을", "300g", "1개"), 11350)
    return {"http_status": 200, **h}


class SharedLowchartTests(unittest.TestCase):
    def test_successful_daangn_observation_can_recover_naver_403(self):
        h = real_history()
        record = create_record(SOURCE_ID, h, NEW_NAME)
        self.assertIsNotNone(record)
        self.assertEqual(record["source_id"], SOURCE_ID)
        self.assertNotIn("affiliate_url", record)
        self.assertEqual(
            verified_history(record, SOURCE_ID, NEW_PID, NEW_IID)["observed_average_krw"],
            15460,
        )
        observed_sleeps = []
        calls = []
        fail = {"http_status": 403, "reason": "HTTP_ERROR",
                "usable_historical_reference": False}
        def reader(mid, token):
            calls.append(mid)
            return record if len(calls) == 3 else None
        with patch("gh_only.lowchart_shared_reference.time.sleep",
                   side_effect=observed_sleeps.append):
            recovered = maybe_share_or_recover(
                SOURCE_ID, NEW_PID, NEW_IID, fail, NEW_NAME,
                token="fake-token", reader=reader,
                writer=lambda *args: self.fail("must not write unverified"),
            )
        self.assertEqual(recovered["observed_average_krw"], 15460)
        self.assertEqual(calls, [SOURCE_ID] * 3)
        self.assertEqual(observed_sleeps, [4, 4])
        self.assertEqual(WAIT_SECONDS, (0, 4, 4, 4, 4, 4))

    def test_good_observation_writes_once_and_skips_sibling_poll(self):
        data = []
        h = real_history()
        out = maybe_share_or_recover(
            SOURCE_ID, NEW_PID, NEW_IID, h, NEW_NAME,
            token="test-token",
            writer=lambda sid, record, token: data.append((sid, record, token)),
            reader=lambda *args: self.fail("already verified locally"),
        )
        self.assertIsNone(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0][0], SOURCE_ID)
        self.assertEqual(data[0][1]["history"]["observed_average_krw"], 15460)

    def test_never_uses_another_telegram_post_or_item_or_stale_mean(self):
        record = create_record(SOURCE_ID, real_history(), NEW_NAME)
        self.assertIsNone(verified_history(record, SOURCE_ID + 1, NEW_PID, NEW_IID))
        self.assertIsNone(verified_history(record, SOURCE_ID, NEW_PID, "9999999999"))
        self.assertIsNone(verified_history(record, SOURCE_ID, "9999999999", NEW_IID))
        bad = copy.deepcopy(record)
        bad["history"]["observed_average_krw"] = 99999999
        self.assertIsNone(verified_history(bad, SOURCE_ID, NEW_PID, NEW_IID))
        bad = copy.deepcopy(record)
        bad["history"]["source_item_identifiers_present"] = False
        self.assertIsNone(verified_history(bad, SOURCE_ID, NEW_PID, NEW_IID))
        bad = copy.deepcopy(record)
        bad["observed_at_utc"] = (
            datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        self.assertIsNone(verified_history(bad, SOURCE_ID, NEW_PID, NEW_IID))
        bad = copy.deepcopy(record)
        bad["schema"] = "unsafe"
        self.assertIsNone(verified_history(bad, SOURCE_ID, NEW_PID, NEW_IID))
        self.assertIsNone(create_record(SOURCE_ID, {**real_history(), "http_status":403}, NEW_NAME))

    def test_current_price_or_average_without_proof_cannot_be_shared(self):
        h = real_history()
        for modifications in (
            {"observed_average_krw": None, "usable_historical_reference":False},
            {"official_normal_price": True},
            {"visible_exact_item_prices_verified": False},
            {"source_item_identifiers_present": False},
            {"item_id": "9999999999"},
        ):
            with self.subTest(modifications=modifications):
                self.assertIsNone(create_record(
                    SOURCE_ID, {**h, **modifications}, NEW_NAME))
        with patch("gh_only.lowchart_shared_reference.time.sleep") as sleep:
            self.assertIsNone(maybe_share_or_recover(
                SOURCE_ID, NEW_PID, NEW_IID, {},
                NEW_NAME, token="", reader=lambda *args:self.fail("must not read")))
        sleep.assert_not_called()

    def test_price_parser_reuses_verified_photo_html_without_second_get(self):
        from gh_only.default_deal_automation import enrich_deal
        from gh_only.lowchart_price_reference import build_url
        title = H_TITLE.split(" - 31,340원")[0]
        photo = {
            "status": "LOWCHART_EXACT_ITEM_IMAGE",
            "og_title": title,
            "og_url": build_url(PID, IID),
            "og_image": "https://thumbnail7.coupangcdn.com/thumbnails/photo.jpg",
            "lowchart_title_verified": True,
            "image_source": "lowchart_exact_item_public_page",
            "_verified_public_item_html": HIST_HTML,
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value=photo), \
             patch("gh_only.lowchart_price_reference.request.urlopen",
                   side_effect=AssertionError("second GET forbidden")), \
             patch("gh_only.lowchart_shared_reference.maybe_share_or_recover",
                   return_value=None):
            r = enrich_deal(
                DEAL, Path(tmp), resolver=lambda _: IDENTITY,
                photo_downloader=lambda *a: {
                    "status":"IMAGE_SAVED", "saved_name":"item.jpg"},
                owner_tag="AF9668915",
            )
        self.assertTrue(r["history"]["read_from_verified_photo_response"])
        self.assertEqual(r["caption"]["observed_average_krw"], 34575)
        self.assertEqual(r["comparison_price_source"], "lowchart_public_history")


    def test_naver_403_can_use_daangn_verified_average_same_exact_item(self):
        # Proves the real incident type: Naver URL fails with HTTP 403;
        # Daangn recorded the same Telegram source+exact SKU's visible mean.
        from urllib.error import HTTPError
        h = {"http_status": 200, **parse_reference(
            HIST_HTML, build_url(PID, IID), PID, IID,
            ("휘카레", "230g", "20개"), 12050)}
        self.assertIsNotNone(create_record(DEAL["source_message_id"], h,
                                            H_TITLE.split(" - 31,340원")[0]))
        representative = {
            "status": "IMAGE_SAVED",
            "saved_name": "representative.jpg",
            "image_source": "fallcent_search_representative",
            "representative_fallcent_image": True,
            "image_match_basis": "UNVERIFIED_FALLCENT_SEARCH_RESULT",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value={
                       "status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                       "http_status": 403, "og_image": None,
                   }), \
             patch("gh_only.lowchart_price_reference.request.urlopen",
                   side_effect=HTTPError(
                       build_url(PID, IID), 403, "Forbidden", None, None)), \
             patch("gh_only.lowchart_shared_reference.maybe_share_or_recover",
                   return_value=h) as recovery, \
             patch("gh_only.fallcent_search_photo.fetch_fallcent_product_photo",
                   return_value=representative):
            out = enrich_deal(
                DEAL, Path(tmp), resolver=lambda _: IDENTITY,
                owner_tag="AF9668915",
                fallcent_high_lookup=lambda *args:
                    self.fail("verified sibling average makes Fallcent unnecessary"),
            )
        recovery.assert_called_once()
        self.assertEqual(out["caption"]["observed_average_krw"], 34575)
        self.assertEqual(out["comparison_price_source"], "lowchart_public_history")
        self.assertTrue(out["history"]["shared_from_verified_sibling"])
        self.assertEqual(out["product_identity"]["item_id"], IID)
        self.assertFalse(out["affsdp"]["official_link_issued"])


if __name__ == "__main__":
    unittest.main()
