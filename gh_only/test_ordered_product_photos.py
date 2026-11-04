"""Offline tests: no public sites, account logins, state writes or posting."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.ordered_product_photos import (
    _safe_image_url, _compatible_title, _dangapick_rows,
    lookup_couspi_photo, lookup_dangapick_photo,
    ordered_external_photo,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.jirum_manual_queue import permanent_lowchart_failure, validate_draft

PID, IID, VID = "8522680027", "24674417324", "91691060708"
NAME = "바디인솔 프리미엄 무지 중목 양말 20켤레"
CDN = ("https://t1a.coupangcdn.com/thumbnails/remote/492x492ex/"
       "image/vendor_inventory/d9a5/eeec934d6400a722236ebab49304cc0f6d32249bc166f83d859f2ddea683.png")
IDENTITY = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": PID,
            "item_id": IID, "vendor_item_id": None}
DEAL = {
    "source_message_id": 94975,
    "source_post_url": "https://t.me/jirum/94975",
    "title": "☆특가 9,700원",
    "source_text": ("☆특가 9,700원\n" + NAME +
                    "\nhttps://link.coupang.com/a/iofnG65WFM"),
    "sale_price_krw": 9700,
    "original_purchase_url": "https://link.coupang.com/a/iofnG65WFM",
}


def index():
    return {"products": [
        {"id": int(PID), "item": 24674417319, "vendor": 91691060725,
         "title": NAME, "key": "8522680027:24674417319:91691060725"},
        {"id": 9999911111, "item": 24674417324, "vendor": 91691060708,
         "title": NAME, "key": "9999911111:24674417324:91691060708"},
    ]}


def fake_saved(img, provider, pid, folder, mid, opener=None):
    f = folder / f"jirum_{mid}_{provider}_photo.jpg"
    f.write_bytes(b"\xff\xd8\xff" + b"x" * 1250)
    return {"status": "IMAGE_SAVED", "saved_name": f.name, "bytes": f.stat().st_size}


class OrderedProductPhotoTests(unittest.TestCase):
    def test_image_hosts_restrict_outside_sources(self):
        self.assertTrue(_safe_image_url(CDN, "dangapick", PID))
        self.assertTrue(_safe_image_url(
            f"https://image.couspi.com/cdn/{PID}/vendor_inventory/x.png",
            "couspi", PID))
        self.assertFalse(_safe_image_url("https://evil.com/a.png", "dangapick", PID))
        self.assertFalse(_safe_image_url(
            "https://image.couspi.com/cdn/99999/abc.png", "couspi", PID))
        self.assertFalse(_safe_image_url("http://t1a.coupangcdn.com/a.jpg", "dangapick", PID))
        self.assertFalse(_safe_image_url(
            "https://t1a.coupangcdn.com.evil.com/a.jpg", "dangapick", PID))

    def test_candidate_index_filters_other_product_ids(self):
        rows = _dangapick_rows(index(), PID, NAME)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item"], 24674417319)
        self.assertFalse(_compatible_title(NAME, "나이키 바지 1개"))

    def test_dangapick_index_only_downloads_matching_product_photo(self):
        # No vendor ID, as in the 94975 Telegram deal.
        page = ("https://dangapick.com/p/" +
                "8522680027-24674417319-91691060725")
        html = (f"<html><h1>{NAME}</h1><img src='/img/logo.png' alt='단가픽'>"
                f"<img src='{CDN}' alt='{NAME}'></html>")
        def fetch(url, host, size_limit, opener):
            if url.endswith("products.json"):
                return json.dumps(index(), ensure_ascii=False).encode()
            self.assertEqual(url, page)
            return html.encode()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gh_only.ordered_product_photos._fetch", side_effect=fetch):
                with patch("gh_only.ordered_product_photos._download",
                           side_effect=fake_saved):
                    result = lookup_dangapick_photo(
                        IDENTITY, NAME, Path(tmp), 94975)
            self.assertEqual(result["status"], "IMAGE_SAVED")
            self.assertEqual(result["image_source"], "dangapick_product_page")
            self.assertEqual(result["image_provider_product_id"], PID)
            self.assertEqual(result["image_provider_item_id"], "24674417319")

    def test_dangapick_does_not_use_unrelated_index_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gh_only.ordered_product_photos._fetch",
                       return_value=json.dumps({"products": [
                           {"id": 111111, "title": NAME, "item": 11111,
                            "vendor": 22222}]}).encode()):
                result = lookup_dangapick_photo(IDENTITY, NAME, Path(tmp), 94975)
        self.assertEqual(result["status"], "NO_VERIFIED_IMAGE")

    def test_provider_chain_is_exact_and_short_circuits_on_success(self):
        calls = []
        def blocked(*args):
            calls.append("couspi")
            return {"status": "SOURCE_UNAVAILABLE", "http_status": 403}
        def found(identity, title, folder, source_id):
            calls.append("dangapick")
            return {"status": "IMAGE_SAVED", "saved_name": "abc.jpg",
                    "image_source": "dangapick_product_page",
                    "image_provider_verified": True,
                    "image_provider_product_id": PID,
                    "image_provider_title": NAME,
                    "image_provider_item_id": "24674417319"}
        with tempfile.TemporaryDirectory() as tmp:
            result = ordered_external_photo(IDENTITY, NAME, Path(tmp), 94975,
                                            couspi=blocked, dangapick=found)
        self.assertEqual(calls, ["couspi", "dangapick"])
        self.assertEqual(result["status"], "IMAGE_SAVED")
        self.assertEqual(len(result["photo_provider_attempts"]), 2)

    def test_all_sources_fail_means_skip_and_no_post(self):
        def nope(*args):
            return {"status": "SOURCE_UNAVAILABLE"}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            result = ordered_external_photo(IDENTITY, NAME, folder, 94975,
                                            couspi=nope, dangapick=nope)
            self.assertEqual(result["status"], "PHOTO_EVIDENCE_UNAVAILABLE")
            self.assertEqual(len(result["photo_provider_attempts"]), 2)
            self.assertEqual(list(folder.iterdir()), [])

    def test_couspi_403_does_not_break_queue(self):
        from urllib.error import HTTPError
        def forbidden(req, timeout):
            raise HTTPError(req.full_url, 403, "Forbidden", {}, None)
        with tempfile.TemporaryDirectory() as tmp:
            result = lookup_couspi_photo(IDENTITY, NAME, Path(tmp), 94975,
                                         opener=forbidden)
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(result["http_status"], 403)

    def test_pipeline_uses_verified_dangapick_image_and_draft_validates(self):
        def provider(identity, title, folder, source_id):
            fake_saved(CDN, "dangapick", PID, folder, source_id)
            return {
                "status": "IMAGE_SAVED",
                "image_source": "dangapick_product_page",
                "saved_name": "jirum_94975_dangapick_photo.jpg",
                "bytes": 1253,
                "image_provider_verified": True,
                "image_provider_product_id": PID,
                "image_provider_item_id": "24674417319",
                "image_provider_title": NAME,
                "photo_provider_attempts": [
                    {"provider":"couspi","status":"SOURCE_UNAVAILABLE"},
                    {"provider":"dangapick","status":"IMAGE_SAVED"}],
            }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "COUPANG_PARTNER_LPTAG": "AF9668915"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={"status":"LOWCHART_ITEM_PAGE_NOT_FOUND",
                                         "http_status":404}):
                    with patch("gh_only.ordered_product_photos.ordered_external_photo",
                               side_effect=provider):
                        row = enrich_deal(
                            DEAL, folder, resolver=lambda _: IDENTITY,
                            history_fetch=lambda *x: {},
                            owner_tag="AF9668915")
                self.assertEqual(row["photo"]["status"], "IMAGE_SAVED")
                self.assertEqual(row["image_source"], "dangapick_product_page")
                self.assertEqual(row["product_name_source"], "external_product_id_page")
                self.assertEqual(row["caption"]["sale_price_krw"], 9700)
                self.assertIn("[할인율 확인 중]", row["caption"]["caption"])
                (folder / "drafts.json").write_text(
                    json.dumps({"drafts": [row]}, ensure_ascii=False), "utf-8")
                article = {"source_id": 94975,
                           "image": folder / row["photo"]["saved_name"],
                           "candidate_url": row["composed_affiliate_url"],
                           "body": row["caption"]["caption"],
                           "title": "[쿠팡🔥핫딜] " + row["caption"]["product_name"]}
                with patch("gh_only.jirum_manual_queue.read_one_draft",
                           return_value=article):
                    output = validate_draft(folder, 94975)
                self.assertEqual(output["product_id"], PID)
                self.assertEqual(output["item_id"], IID)

    def test_pipeline_exhausted_records_skip_not_lowchart_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "COUPANG_PARTNER_LPTAG": "AF9668915"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={"status": "LOWCHART_ITEM_PAGE_NOT_FOUND",
                                         "http_status": 404}):
                    with patch("gh_only.ordered_product_photos.ordered_external_photo",
                               return_value={"status": "PHOTO_EVIDENCE_UNAVAILABLE",
                                             "photo_provider_attempts": [
                                                 {"provider": "couspi", "status": "SOURCE_UNAVAILABLE"},
                                                 {"provider": "dangapick", "status": "NO_VERIFIED_IMAGE"}]}):
                        row = enrich_deal(DEAL, folder, resolver=lambda _: IDENTITY,
                                          owner_tag="AF9668915")
                self.assertEqual(row["photo"]["status"], "PHOTO_EVIDENCE_UNAVAILABLE")
                (folder / "drafts.json").write_text(
                    json.dumps({"drafts": [row]}, ensure_ascii=False), "utf-8")
                self.assertEqual(permanent_lowchart_failure(folder),
                                 "PHOTO_EVIDENCE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
