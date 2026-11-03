"""Offline acceptance tests for conditional image-index fallback (no live search)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gh_only.search_product_photo import (
    _coupang_product_id, parse_naver_image_results,
    lookup_search_product_photo, download_search_photo,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.jirum_manual_queue import validate_draft

PID, IID, VID = "202217052", "23987208247", "4548111478"
NAME = "닥터스베스트 글루코사민 콘드로이친 MSM 캡슐, 240정, 1개"
IMAGE = "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/sample.jpg"
ITEM = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": PID,
        "item_id": IID, "vendor_item_id": VID}
RESULT = {"type": "image", "title": NAME + " - 기타건강식품 | 쿠팡",
          "link": "https://www.coupang.com/vp/products/" + PID,
          "originalUrl": IMAGE}
SOURCE = {
    "source_message_id": 94964, "source_post_url": "https://t.me/jirum/94964",
    "title": NAME, "source_text": NAME + "\n☆특가 15,070원",
    "sale_price_krw": 15070,
    "original_purchase_url": "https://link.coupang.com/a/example",
}


def page(*rows):
    return "<html><body><script>window.app={items: " + json.dumps(
        list(rows), ensure_ascii=False) + ", x: 1}</script></body></html>"


class Response:
    def __init__(self, url, content):
        self.url = url
        self.content = content
        self.status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def geturl(self):
        return self.url
    def read(self, n):
        return self.content[:n]


class ImageSearchFallbackTests(unittest.TestCase):
    def test_exact_coupang_product_and_option_selects_cdn_image(self):
        r = parse_naver_image_results(page(RESULT), ITEM, NAME)
        self.assertEqual(r["status"], "SEARCH_VERIFIED_COUPANG_PRODUCT_IMAGE")
        self.assertEqual(r["og_image"], IMAGE)
        self.assertTrue(r["image_search_product_id_verified"])
        self.assertTrue(r["image_search_title_verified"])
        self.assertFalse(r["image_search_exact_item_id_verified"])
        self.assertEqual(r["og_title"], NAME)

    def test_other_coupang_product_id_never_used(self):
        other = {**RESULT, "link": "https://www.coupang.com/vp/products/8370903873"}
        r = parse_naver_image_results(page(other), ITEM, NAME)
        self.assertEqual(r["status"], "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND")

    def test_other_package_count_never_used(self):
        other = {**RESULT, "title": RESULT["title"].replace("1개", "3개")}
        r = parse_naver_image_results(page(other), ITEM, NAME)
        self.assertEqual(r["status"], "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND")

    def test_untrusted_image_host_and_other_sources_never_used(self):
        bad = {**RESULT, "originalUrl": "https://example.com/product.jpg"}
        self.assertEqual(parse_naver_image_results(
            page(bad), ITEM, NAME)["status"], "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND")
        blog = {**RESULT, "link": "https://blog.naver.com/user/123"}
        self.assertEqual(parse_naver_image_results(
            page(blog), ITEM, NAME)["status"], "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND")

    def test_ambiguous_distinct_photos_not_selected(self):
        other = {**RESULT, "originalUrl": IMAGE.replace("sample.jpg", "other.jpg")}
        self.assertEqual(parse_naver_image_results(
            page(RESULT, other), ITEM, NAME)["status"], "SEARCH_PRODUCT_IMAGE_AMBIGUOUS")

    def test_bad_or_missing_json_fails_closed(self):
        self.assertEqual(parse_naver_image_results(
            "<html>No items</html>", ITEM, NAME)["status"], "SEARCH_RESULTS_MISSING")
        self.assertEqual(parse_naver_image_results(
            "<script>items: [{bad}]</script>", ITEM, NAME)["status"],
            "SEARCH_RESULTS_MALFORMED")

    def test_link_must_be_exact_https_coupang_product(self):
        for u in ("https://evil.example/vp/products/" + PID,
                  "http://www.coupang.com/vp/products/" + PID,
                  "https://www.coupang.com.evil.org/vp/products/" + PID,
                  "https://www.coupang.com/vp/products/000000000",
                  "https://www.coupang.com/vp/products/" + PID + "/review"):
            self.assertIsNone(_coupang_product_id(u))
        self.assertEqual(_coupang_product_id(RESULT["link"]), PID)

    def test_bounded_search_requests_and_no_redirect(self):
        called = []
        def open_result(req, timeout):
            called.append((req.full_url, timeout))
            return Response(req.full_url, page(RESULT).encode())
        result = lookup_search_product_photo(ITEM, NAME, opener=open_result)
        self.assertEqual(result["status"], "SEARCH_VERIFIED_COUPANG_PRODUCT_IMAGE")
        self.assertEqual(len(called), 1)
        self.assertTrue(called[0][0].startswith("https://search.naver.com/search.naver?"))

    def test_missing_match_attempts_two_queries_then_fails_closed(self):
        called = []
        def open_result(req, timeout):
            called.append(req.full_url)
            return Response(req.full_url, page({**RESULT, "link":
                "https://www.coupang.com/vp/products/8370903873"}).encode())
        r = lookup_search_product_photo(ITEM, NAME, opener=open_result)
        self.assertEqual(r["status"], "SEARCH_EXACT_PRODUCT_AND_OPTION_NOT_FOUND")
        self.assertEqual(len(called), 2)
        self.assertIn(PID, called[-1])

    def test_search_photo_download_reuses_existing_cdn_validator(self):
        # Test only the wrapper's source attribution; byte validation is already
        # covered by gh_only.test_lowchart_product_photo.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            file = p / "jirum_94964_lowchart_photo.jpg"
            file.write_bytes(b"\xff\xd8\xff" + b"x" * 1400)
            with patch("gh_only.search_product_photo.download_exact_photo",
                       return_value={"status": "IMAGE_SAVED",
                                     "saved_name": file.name}) as downloader:
                result = download_search_photo(IMAGE, p, 94964)
            self.assertEqual(result["image_source"], "naver_indexed_coupang_product_image")
            self.assertIn("_search_photo", result["saved_name"])
            self.assertTrue((p / result["saved_name"]).is_file())
            self.assertFalse(file.exists())
            downloader.assert_called_once()

    def test_lowchart_missing_uses_verified_search_and_validates_publish_draft(self):
        proof = parse_naver_image_results(page(RESULT), ITEM, NAME)
        def save_fake_photo(url, folder, mid):
            filename = f"jirum_{mid}_search_photo.jpg"
            (folder / filename).write_bytes(b"\xff\xd8\xff" + b"x" * 1400)
            return {"status": "IMAGE_SAVED", "saved_name": filename,
                    "image_source": "naver_indexed_coupang_product_image"}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.dict(os.environ, {
                "JIRUM_ENABLE_SEARCH_PHOTO_FALLBACK": "true",
                "JIRUM_IMAGE_SOURCE": "lowchart",
                "COUPANG_PARTNER_LPTAG": "AF9668915",
            }):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={"status": "LOWCHART_ITEM_PAGE_NOT_FOUND",
                                         "http_status": 404}):
                    row = enrich_deal(
                        SOURCE, folder, resolver=lambda _: ITEM,
                        owner_tag="AF9668915",
                        history_fetch=lambda *args: {"usable_historical_reference": False},
                        search_lookup=lambda *args: proof,
                        search_downloader=save_fake_photo,
                    )
                self.assertEqual(row["search_status"], "SEARCH_VERIFIED_COUPANG_PRODUCT_IMAGE")
                self.assertFalse(row["lowchart_title_verified"])
                self.assertTrue(row["image_search_product_id_verified"])
                self.assertEqual(row["product_name_source"], "search_exact_product_id_index")
                self.assertEqual(row["caption"]["product_name"], NAME)
                self.assertNotIn("%할인]", row["caption"]["caption"])
                self.assertIn("[할인율 확인 중]", row["caption"]["caption"])
                (folder / "drafts.json").write_text(
                    json.dumps({"drafts": [row]}, ensure_ascii=False), "utf-8")
                article = {
                    "source_id": 94964, "image": folder / row["photo"]["saved_name"],
                    "candidate_url": row["composed_affiliate_url"],
                    "body": row["caption"]["caption"],
                    "title": "[쿠팡🔥핫딜] " + row["caption"]["product_name"],
                }
                with patch("gh_only.jirum_manual_queue.read_one_draft",
                           return_value=article):
                    verified = validate_draft(folder, 94964)
                self.assertEqual(verified["product_id"], PID)
                self.assertEqual(verified["item_id"], IID)

    def test_lowchart_photo_success_does_not_start_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JIRUM_ENABLE_SEARCH_PHOTO_FALLBACK": "true"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value={
                               "status": "LOWCHART_EXACT_ITEM_IMAGE",
                               "og_title": NAME,
                               "og_url": "https://www.lowchart.com/" + PID + "-" + IID,
                               "og_image": IMAGE,
                               "lowchart_title_verified": True,
                               "image_source": "lowchart_exact_item_public_page",
                           }):
                    with patch("gh_only.default_deal_automation.download_exact_photo",
                               return_value={"status": "IMAGE_SAVED",
                                             "saved_name": "original.jpg"}):
                        result = enrich_deal(
                            SOURCE, Path(tmp), resolver=lambda _: ITEM,
                            history_fetch=lambda *a: {},
                            owner_tag="AF9668915",
                            search_lookup=lambda *a: (_ for _ in ()).throw(
                                AssertionError("Search fallback should not run")),
                        )
        self.assertEqual(result["search_status"], "NOT_ATTEMPTED")
        self.assertEqual(result["image_source"], "lowchart_exact_item_public_page")


if __name__ == "__main__":
    unittest.main()
