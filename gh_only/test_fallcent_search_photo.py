"""Offline regression: photo priority Lowchart -> Fallcent -> Naver.

No HTTP calls or Daangn submissions; includes a realistic Fallcent search-card
fixture and verifies the exact production pre-submit draft contract.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.fallcent_search_photo import (
    choose_representative_card, fetch_fallcent_product_photo, IMAGE_SOURCE,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.jirum_manual_queue import validate_draft, permanent_lowchart_failure
from gh_only.lowchart_price_reference import build_url

TITLE = "26년 신안 천일염 햇소금10kg 태평염전 간수뺀, 1개"
IMG_URL = ("https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/"
           "image/vendor_inventory/f72a/5ada27166b2c69351d396ab8ba7aa5fac96f6863422d9efc72208cd93e14.png")
HTML = f"""<!doctype html><html><head>
<meta property="og:image" content="https://thumbnail.coupangcdn.com/unrelated.png">
</head><body>
<a href="https://fcnt.link/unrelated">
  <img src="{IMG_URL}" alt="different brand">
  26년산 신안 햇소금 굵은소금 20kg 1개 17,420원
</a>
<a href="https://fcnt.link/salt10">
  <img src="{IMG_URL}" alt="salt">
  <span>역대급 최저가 판매자배송</span>
  <span>태평염전 간수뺀 햇소금 신안 천일염 탈수, 10kg, 1개</span>
  <span>9,580원</span>
</a></body></html>"""
SALT_ID = {"status": "EXACT_ITEM_IDENTIFIED",
           "product_id": "8863489459", "item_id": "28741784521",
           "vendor_item_id": "999999777"}
DEAL = {
    "source_message_id": 96123,
    "source_post_url": "https://t.me/jirum/96123",
    "original_purchase_url": "https://link.coupang.com/a/ioWODkPbz2",
    "source_text": "☆특가 11,900원\n" + TITLE +
                   "\n📦 특가 바로가기\nhttps://link.coupang.com/a/ioWODkPbz2",
    "title": TITLE,
    "sale_price_krw": 11900,
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 1550


class FakeResponse:
    status = 200
    def __init__(self, url, content, mime):
        self.url, self.content, self.mime = url, content, mime
        self.headers = self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.url
    def get_content_type(self): return self.mime
    def read(self, n=-1): return self.content[:n]


class FallcentTests(unittest.TestCase):
    def test_same_10kg_photo_beats_first_wrong_20kg_and_unrelated_og_image(self):
        photo = choose_representative_card(HTML, TITLE)
        self.assertIsNotNone(photo)
        self.assertEqual(photo["image"], IMG_URL)
        self.assertEqual(photo["href"], "https://fcnt.link/salt10")
        self.assertIn("10kg", photo["title"])

    def test_wrong_weight_or_foreign_cdn_does_not_pass(self):
        only_wrong = HTML.split('<a href="https://fcnt.link/salt10">')[0] + "</body></html>"
        self.assertIsNone(choose_representative_card(only_wrong, TITLE))
        self.assertIsNone(choose_representative_card(
            HTML.replace(IMG_URL, "https://evil.example/photos/salt.png"), TITLE))

    def test_fallcent_public_search_downloads_image_and_keeps_provenance(self):
        calls = []
        def opener(req, timeout):
            url = req.full_url
            calls.append(url)
            if "fallcent.com/product/search/" in url:
                return FakeResponse(url, HTML.encode("utf-8"), "text/html")
            if url == IMG_URL:
                return FakeResponse(url, PNG, "image/png")
            raise AssertionError("Unexpected GET " + url)
        with tempfile.TemporaryDirectory() as tmp:
            row = fetch_fallcent_product_photo(TITLE, Path(tmp), 96123, opener=opener)
            self.assertEqual(row["status"], "IMAGE_SAVED")
            self.assertEqual(row["image_source"], IMAGE_SOURCE)
            self.assertTrue(row["representative_fallcent_image"])
            self.assertEqual((Path(tmp)/row["saved_name"]).read_bytes(), PNG)
            self.assertEqual(row["saved_name"], "jirum_96123_fallcent_photo.png")
            self.assertEqual(len(calls), 2)

    def test_lowchart_missing_fallcent_used_naver_not_called_draft_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            def fallcent(title, output, mid):
                self.assertEqual(title, TITLE)
                fn = f"jirum_{mid}_fallcent_photo.png"
                (output/fn).write_bytes(PNG)
                return {"status": "IMAGE_SAVED", "saved_name": fn,
                        "bytes": len(PNG), "image_source": IMAGE_SOURCE,
                        "image_match_basis": "UNVERIFIED_FALLCENT_SEARCH_RESULT",
                        "representative_fallcent_image": True,
                        "image_provider_verified": False,
                        "image_provider_title": "태평염전 간수뺀 햇소금 신안 천일염 탈수, 10kg, 1개"}
            with (patch.dict("os.environ", {"JIRUM_IMAGE_SOURCE": "lowchart",
                                            "JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "false"}),
                  patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                        return_value={"status":"LOWCHART_ITEM_PAGE_NOT_FOUND",
                                      "http_status": 404}),
                  patch("gh_only.naver_web_image_photo.fetch_first_naver_photo",
                        side_effect=AssertionError("Naver must not run"))):
                row = enrich_deal(DEAL, folder, resolver=lambda _: SALT_ID,
                                  history_fetch=lambda *a: {},
                                  owner_tag="AF9668915", fallcent_lookup=fallcent)
            self.assertEqual(row["image_source"], IMAGE_SOURCE)
            self.assertTrue(row["representative_fallcent_image"])
            self.assertEqual([x["provider"] for x in row["photo_provider_attempts"]],
                             ["fallcent_search"])
            self.assertEqual(row["caption"]["sale_price_krw"], 11900)
            self.assertEqual(row["caption"]["product_name"], TITLE)
            self.assertEqual(row["product_name_source"], "telegram_product_title")
            self.assertIsNone(row["caption"]["observed_average_krw"])
            (folder/"drafts.json").write_text(json.dumps({
                "schema": "github-jirum-default-price-photo-affsdp-draft-v2",
                "drafts": [row],
            }, ensure_ascii=False), encoding="utf-8")
            article = validate_draft(folder, 96123)
            self.assertIn("폴센트 상품 검색 대표 사진", article["body"])
            self.assertEqual(article["image"].name, "jirum_96123_fallcent_photo.png")
            self.assertIsNone(permanent_lowchart_failure(folder))

    def test_lowchart_first_skips_fallcent_and_naver(self):
        og = {"status": "LOWCHART_EXACT_ITEM_IMAGE",
              "og_url": build_url(SALT_ID["product_id"], SALT_ID["item_id"]),
              "og_title": TITLE, "lowchart_title_verified": True,
              "image_source": "lowchart_exact_item_public_page",
              "og_image": IMG_URL}
        with (tempfile.TemporaryDirectory() as tmp,
              patch.dict("os.environ", {"JIRUM_IMAGE_SOURCE": "lowchart",
                                        "JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "false"}),
              patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                    return_value=og),
              patch("gh_only.naver_web_image_photo.fetch_first_naver_photo",
                    side_effect=AssertionError("No Naver for exact Lowchart"))):
            row = enrich_deal(DEAL, Path(tmp), resolver=lambda _: SALT_ID,
                              history_fetch=lambda *a: {},
                              photo_downloader=lambda *a: {
                                  "status": "IMAGE_SAVED", "saved_name": "anything.jpg",
                              },
                              owner_tag="AF9668915",
                              fallcent_lookup=lambda *a: (_ for _ in ()).throw(
                                  AssertionError("No Fallcent for exact Lowchart")))
        self.assertEqual(row["image_source"], "lowchart_exact_item_public_page")
        self.assertEqual(row["photo_provider_attempts"], [])

    def test_fallcent_failure_runs_naver_third(self):
        calls = []
        def fallcent(*args):
            calls.append("fallcent")
            return {"status": "FALLCENT_NO_MATCHING_IMAGE"}
        def naver(title, folder, mid):
            calls.append("naver")
            return {"status": "IMAGE_SAVED",
                    "saved_name": f"jirum_{mid}_naver_web_photo.png",
                    "bytes": len(PNG),
                    "image_source": "naver_web_first_image",
                    "image_match_basis": "UNVERIFIED_FIRST_NAVER_IMAGE_RESULT"}
        with (tempfile.TemporaryDirectory() as tmp,
              patch.dict("os.environ", {"JIRUM_IMAGE_SOURCE": "lowchart",
                                        "JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "false"}),
              patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                    return_value={"status": "LOWCHART_ITEM_PAGE_NOT_FOUND"}),
              patch("gh_only.naver_web_image_photo.fetch_first_naver_photo",
                    side_effect=naver)):
            row = enrich_deal(DEAL, Path(tmp), resolver=lambda _: SALT_ID,
                              history_fetch=lambda *a: {},
                              owner_tag="AF9668915", fallcent_lookup=fallcent)
        self.assertEqual(calls, ["fallcent", "naver"])
        self.assertEqual(row["image_source"], "naver_web_first_image")
        self.assertTrue(row["representative_naver_image"])
        self.assertEqual([x["provider"] for x in row["photo_provider_attempts"]],
                         ["fallcent_search", "naver_web"])


if __name__ == "__main__":
    unittest.main()
