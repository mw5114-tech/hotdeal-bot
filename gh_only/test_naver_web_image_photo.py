"""Offline tests for first-result Naver image fallback and manual replay."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from gh_only.naver_web_image_photo import (
    fetch_first_naver_photo, _safe_image_url
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.jirum_manual_queue import (
    validate_draft, permanent_lowchart_failure, freshness_status
)

TITLE = ("삼성전자 4K UHD QLED TV, 214cm(85인치), "
         "KQ85QF6AAFXKR, 스탠드형, 방문설치")
ID = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": "8870769472",
      "item_id": "25880144275", "vendor_item_id": "92866178655"}
DEAL = {
    "source_message_id": 94986,
    "source_post_url": "https://t.me/jirum/94986",
    "original_purchase_url": "https://link.coupang.com/a/ioqiG4QVPM",
    "source_text": ("☆특가 1,791,000원 (11.8% 할인)\n"+TITLE+
                    "\n📌 가격 현황\n- 정상가: 2,031,500원\n"
                    "- 할인: 240,500원\n📦 특가 바로가기\n"
                    "https://link.coupang.com/a/ioqiG4QVPM"),
    "title": TITLE, "sale_price_krw": 1791000
}
SEARCH_URL = "https://search.pstatic.net/common/?src=https%3A%2F%2Fexample.org%2Fimage.jpg&type=a340"
IMG = b"\xff\xd8\xff" + b"\0" * 1600


class FakeResponse:
    status = 200
    def __init__(self, url, body, mime="text/html"):
        self.url, self.body, self.mime = url, body, mime
        self.headers = self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.url
    def get_content_type(self): return self.mime
    def read(self, n=-1): return self.body[:n]


class NaverPhotoTests(unittest.TestCase):
    def test_first_image_link_thumb_always_wins_regardless_of_option(self):
        html = (f"<html><img src='https://search.pstatic.net/logo.jpg'>"
                f"<a class='link_thumb loaded'><img src='{SEARCH_URL}' alt='다른 옵션 상품'></a>"
                f"<a class='link_thumb'><img src='{SEARCH_URL}' alt='두번째'></a></html>")
        calls = []
        def opener(req, timeout):
            url = req.full_url
            calls.append(url)
            if "m.search.naver.com" in url:
                return FakeResponse(url, html.encode())
            return FakeResponse(url, IMG, "image/jpeg")
        with tempfile.TemporaryDirectory() as tmp:
            row = fetch_first_naver_photo(TITLE, Path(tmp), 94986, opener=opener)
            self.assertEqual(row["status"], "IMAGE_SAVED")
            self.assertEqual(row["image_source"], "naver_web_first_image")
            self.assertFalse(row["image_provider_verified"])
            self.assertEqual(row["image_provider_title"], "다른 옵션 상품")
            self.assertEqual((Path(tmp)/row["saved_name"]).read_bytes(), IMG)
        self.assertEqual(len(calls), 2)
        self.assertIn("KQ85QF6AAFXKR", calls[0])

    def test_host_check_rejects_untrusted_and_non_thumbnail(self):
        self.assertTrue(_safe_image_url(SEARCH_URL))
        self.assertFalse(_safe_image_url("https://evil.com/common/?src=x"))
        self.assertFalse(_safe_image_url("http://search.pstatic.net/common/?src=x"))
        self.assertFalse(_safe_image_url("https://search.pstatic.net.evil.org/common/?src=x"))

    def test_no_image_is_not_published(self):
        url = "https://m.search.naver.com/search.naver"
        def opener(req, timeout):
            return FakeResponse(req.full_url, b"<html>no results</html>")
        with tempfile.TemporaryDirectory() as tmp:
            d = fetch_first_naver_photo(TITLE, Path(tmp), 94986, opener=opener)
            self.assertEqual(d["status"], "NAVER_FIRST_IMAGE_MISSING")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_pipeline_uses_telegram_title_naver_photo_and_preserves_price(self):
        def fake_fetch(title, folder, mid):
            self.assertEqual(title, TITLE)
            filename = f"jirum_{mid}_naver_web_photo.jpg"
            (folder/filename).write_bytes(IMG)
            return {
                "status": "IMAGE_SAVED",
                "saved_name": filename,
                "bytes": len(IMG),
                "image_source": "naver_web_first_image",
                "image_match_basis": "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT",
                "image_provider_verified": False,
                "image_provider_title": "다른 옵션 상품",
                "image_provider_page": "https://m.search.naver.com/search.naver",
            }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.dict(os.environ, {
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "COUPANG_PARTNER_LPTAG": "AF9668915",
            }):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={"status":"LOWCHART_ITEM_PAGE_NOT_FOUND",
                                         "http_status": 404}):
                    with patch("gh_only.naver_web_image_photo.fetch_first_naver_photo",
                               side_effect=fake_fetch):
                        row = enrich_deal(
                            DEAL, folder, resolver=lambda _: ID,
                            history_fetch=lambda *args: {},
                            owner_tag="AF9668915")
                self.assertEqual(row["photo"]["status"], "IMAGE_SAVED")
                self.assertEqual(row["product_name_source"], "telegram_product_title")
                self.assertEqual(row["image_source"], "naver_web_first_image")
                self.assertTrue(row["representative_naver_image"])
                self.assertFalse(row["image_provider_verified"])
                self.assertEqual(row["caption"]["product_name"], TITLE)
                self.assertEqual(row["caption"]["sale_price_krw"], 1791000)
                self.assertIsNone(row["caption"]["observed_average_krw"])
                self.assertIn("쿠팡 평소 판매가 : 확인 중", row["caption"]["caption"])
                self.assertEqual(row["caption"]["approx_discount_vs_historical_mean_pct"], None)
                (folder/"drafts.json").write_text(json.dumps({
                    "schema":"github-jirum-default-price-photo-affsdp-draft-v2",
                    "drafts":[row],
                },ensure_ascii=False),encoding="utf-8")
                article = validate_draft(folder, 94986)
                self.assertEqual(article["source_id"], 94986)
                self.assertEqual(article["image"].name, "jirum_94986_naver_web_photo.jpg")
                self.assertIn("대표 사진",article["body"])
                self.assertIn("92866178655",article["candidate_url"])
                self.assertIsNone(permanent_lowchart_failure(folder))

    def test_previously_skipped_photo_exhaustion_is_still_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/"drafts.json").write_text(json.dumps({
                "drafts":[{"source_message_id":94986,
                           "product_identity":ID,
                           "photo":{"status":"PHOTO_EVIDENCE_UNAVAILABLE"},
                           "image_metadata_status":"LOWCHART_ITEM_PAGE_NOT_FOUND"}]
            }),encoding="utf-8")
            self.assertEqual(permanent_lowchart_failure(folder),
                             "PHOTO_EVIDENCE_UNAVAILABLE")

    def test_user_replay_exemption_only_for_approved_ids(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.assertEqual(freshness_status({
            "id":94986, "posted_at_utc":old,
            "manual_replay_approved":True}),"FRESH")
        self.assertEqual(freshness_status({
            "id":94991, "posted_at_utc":old,
            "manual_replay_approved":True}),"DEAL_OVER_60_MINUTES_OLD")
        self.assertEqual(freshness_status({
            "id":94986, "posted_at_utc":old}),"DEAL_OVER_60_MINUTES_OLD")


if __name__ == "__main__":
    unittest.main()
