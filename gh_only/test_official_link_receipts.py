"""Offline contract tests for Muse -> GitHub official shortlink receipts."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from gh_only.official_link_receipts import (
    load_receipt, official_url, validate_receipt,
)


NOW = datetime(2026, 11, 3, 1, 30, tzinfo=timezone.utc)
ROW = {"id": 94890, "posted_at_utc": "2026-11-03T01:10:00+00:00"}


def receipt():
    return {
        "telegram_message_id": 94890,
        "telegram_posted_at": "2026-11-03T10:10:00+09:00",
        "product_id": "9440621860",
        "item_id": "28512619980",
        "partners_link": "https://link.coupang.com/a/il321hccMe",
        "issued_at": "2026-11-03T01:15:00Z",
        "status": "LINK_READY",
    }


class OfficialLinkReceiptsTests(unittest.TestCase):
    def test_valid_official_link(self):
        self.assertEqual(validate_receipt(receipt(), ROW, now=NOW)["item_id"], "28512619980")

    def test_missing_does_not_allow_candidate_fallback(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(load_receipt(ROW, Path(t), now=NOW))

    def test_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "94890.json").write_text(json.dumps(receipt()), encoding="utf-8")
            self.assertEqual(load_receipt(ROW, Path(t), now=NOW)["status"], "LINK_READY")

    def test_wrong_telegram_id_rejected(self):
        d = receipt()
        d["telegram_message_id"] = 94891
        with self.assertRaisesRegex(ValueError, "MESSAGE_ID_MISMATCH"):
            validate_receipt(d, ROW, now=NOW)

    def test_wrong_original_post_time_rejected(self):
        d = receipt()
        d["telegram_posted_at"] = "2026-11-03T01:09:59Z"
        with self.assertRaisesRegex(ValueError, "TIMESTAMP_INVALID"):
            validate_receipt(d, ROW, now=NOW)

    def test_future_issue_time_rejected(self):
        d = receipt()
        d["issued_at"] = (NOW + timedelta(hours=2)).isoformat()
        with self.assertRaisesRegex(ValueError, "ISSUED_AT_INCONSISTENT"):
            validate_receipt(d, ROW, now=NOW)

    def test_unauthorized_or_fake_url_rejected(self):
        for url in [
            "https://link.coupang.com/re/AFFSDP?lptag=AF9668915",
            "https://www.coupang.com/vp/products/9440621860",
            "http://link.coupang.com/a/il321hccMe",
            "https://link.coupang.com.evil.example/a/il321hccMe",
            "https://link.coupang.com/a/il321hccMe?tracking=1",
            "https://link.coupang.com/a/il321hccMe#fragment",
        ]:
            self.assertFalse(official_url(url), url)
            d = receipt()
            d["partners_link"] = url
            with self.assertRaisesRegex(ValueError, "URL_INVALID"):
                validate_receipt(d, ROW, now=NOW)

    def test_unrecognized_fields_rejected(self):
        d = receipt()
        d["cookie"] = "should-not-be-here"
        with self.assertRaisesRegex(ValueError, "FIELDS_INVALID"):
            validate_receipt(d, ROW, now=NOW)

    def test_unissued_status_not_accepted(self):
        d = receipt()
        d["status"] = "PENDING"
        with self.assertRaisesRegex(ValueError, "NOT_READY"):
            validate_receipt(d, ROW, now=NOW)


if __name__ == "__main__":
    unittest.main()
