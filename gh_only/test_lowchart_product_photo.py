"""Offline exact-item public Lowchart product-photo tests. No HTTP requests."""
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from gh_only.lowchart_product_photo import (
    is_coupang_cdn_url, parse_item_photo, lookup_exact_item_photo,
    download_exact_photo,
)

PID = "8178921473"
IID = "23378986290"
NAME = "유에이지 맥세이프 그립톡 스마트링 거치대, 블랙 + 실버, 1개"
URL = "https://thumbnail7.coupangcdn.com/thumbnails/remote/492x492ex/image/abcdef1234.jpg"
ITEM = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": PID,
        "item_id": IID, "vendor_item_id": "91234567890"}
BASE_HTML = (
    "<html><head><title>" + NAME + " - 39,150원 - 로우차트</title>"
    "</head><body>product " + PID + " item " + IID +
    '<img src="https://chart.lowchart.com/a.jpg" alt="가격 변동 차트">'
    '<img src="' + URL + '" alt="' + NAME + '">'
    "</body></html>"
)


class MockResponse:
    status = 200
    def __init__(self, url, payload, mime="text/html"):
        self.url = url
        self.payload = payload
        self.headers = self
        self.mime = mime
    def geturl(self): return self.url
    def read(self, n=-1): return self.payload[:n]
    def get_content_type(self): return self.mime
    def __enter__(self): return self
    def __exit__(self, *args): return False


class LowchartProductPhotoTests(unittest.TestCase):
    def test_exact_title_and_item_yield_one_real_cdn_product_photo(self):
        actual = parse_item_photo(BASE_HTML, ITEM, NAME)
        self.assertEqual(actual["status"], "LOWCHART_EXACT_ITEM_IMAGE")
        self.assertEqual(actual["og_url"], f"https://www.lowchart.com/{PID}-{IID}")
        self.assertEqual(actual["og_image"], URL)
        self.assertEqual(actual["og_title"], NAME)

    def test_reject_mismatched_item_id_even_with_matching_photo(self):
        self.assertEqual(parse_item_photo(BASE_HTML.replace(IID, "99999999999"), ITEM, NAME)
                         ["status"], "LOWCHART_ITEM_IDS_MISSING")

    def test_lowchart_can_add_model_or_color_to_generic_exact_item_title(self):
        html = BASE_HTML.replace(
            NAME, NAME + ", HD9137/90"
        )
        result = parse_item_photo(html, ITEM, NAME)
        self.assertEqual(result["status"], "LOWCHART_EXACT_ITEM_IMAGE")

    def test_allow_different_package_count_when_exact_product_item_ids_match(self):
        # An option mismatch must not block the image or the article:
        # the source link productId/itemId, not its displayed text, is key.
        source = NAME.replace(", 1개", ", 2개")
        result = parse_item_photo(BASE_HTML, ITEM, source)
        self.assertEqual(result["status"], "LOWCHART_EXACT_ITEM_IMAGE")
        self.assertFalse(result["image_same_named_option"])
        self.assertEqual(result["image_match_basis"], "EXACT_LOWCHART_PRODUCT_ID_AND_ITEM_ID")
        self.assertEqual(result["og_image"], URL)

    def test_allow_different_color_or_completely_different_name(self):
        for source in (
            NAME.replace("블랙 + 실버", "화이트"),
            "다른 색상과 상품명, 500ml, 3개",
        ):
            with self.subTest(source=source):
                actual = parse_item_photo(BASE_HTML, ITEM, source)
                self.assertEqual(actual["status"], "LOWCHART_EXACT_ITEM_IMAGE")
                self.assertFalse(actual["image_same_named_option"])

    def test_reject_different_product_id_even_if_image_looks_valid(self):
        changed = BASE_HTML.replace(PID, "9999999999")
        self.assertEqual(
            parse_item_photo(changed, ITEM, NAME)["status"],
            "LOWCHART_ITEM_IDS_MISSING",
        )

    def test_reject_missing_page_title_even_if_photo_matches_source(self):
        page = BASE_HTML.replace(
            NAME + " - 39,150원 - 로우차트", " - 39,150원 - 로우차트"
        )
        self.assertEqual(parse_item_photo(page, ITEM, NAME)["status"],
                         "LOWCHART_PAGE_PRODUCT_TITLE_MISSING")

    def test_reject_image_alt_not_matching_own_lowchart_page_title(self):
        page = BASE_HTML.replace('alt="' + NAME + '"', 'alt="다른 추천 상품"')
        self.assertEqual(parse_item_photo(page, ITEM, NAME)["status"],
                         "LOWCHART_PRODUCT_IMAGE_NOT_FOUND")

    def test_reject_ads_images_chart_and_duplicate_matching_imgs(self):
        only_chart = BASE_HTML.replace('<img src="' + URL + '" alt="' + NAME + '">', "")
        self.assertEqual(parse_item_photo(only_chart, ITEM, NAME)["status"],
                         "LOWCHART_PRODUCT_IMAGE_NOT_FOUND")
        extra = BASE_HTML.replace("</body>", '<img src="' + URL +
                                  '" alt="' + NAME + '"></body>')
        self.assertEqual(parse_item_photo(extra, ITEM, NAME)["status"],
                         "LOWCHART_PRODUCT_IMAGE_AMBIGUOUS")

    def test_cdn_allowlist_and_no_leaky_urls(self):
        self.assertTrue(is_coupang_cdn_url(URL))
        for url in [
            "https://thumbnail7.coupangcdn.com.evil.org/pic.jpg",
            "http://thumbnail7.coupangcdn.com/pic.jpg",
            "https://thumbnail7.coupangcdn.com@evil.org/pic.jpg",
            "https://chart.lowchart.com/a.jpg",
            "https://thumbnail7.coupangcdn.com/malware.exe",
        ]:
            with self.subTest(url=url):
                self.assertFalse(is_coupang_cdn_url(url))

    def test_lowchart_exact_page_http_response_required(self):
        actual_url = f"https://www.lowchart.com/{PID}-{IID}"
        def opener(req, timeout):
            self.assertEqual(req.full_url, actual_url)
            return MockResponse(actual_url, BASE_HTML.encode())
        self.assertEqual(lookup_exact_item_photo(ITEM, NAME, opener=opener)
                         ["status"], "LOWCHART_EXACT_ITEM_IMAGE")
        bad = lambda req, timeout: MockResponse("https://evil.example/item", BASE_HTML.encode())
        self.assertEqual(lookup_exact_item_photo(ITEM, NAME, opener=bad)
                         ["status"], "LOWCHART_UNEXPECTED_RESPONSE")

    def test_download_validates_image_bytes_and_saves_exact_id_name(self):
        def opener(req, timeout):
            return MockResponse(URL, bytes((255,216,255)) + b"a" * 1500, "image/jpeg")
        with tempfile.TemporaryDirectory() as tmp:
            saved = download_exact_photo(URL, Path(tmp), 94789, opener=opener)
            self.assertEqual(saved["status"], "IMAGE_SAVED")
            self.assertEqual(saved["saved_name"], "jirum_94789_lowchart_photo.jpg")
            self.assertTrue((Path(tmp) / saved["saved_name"]).is_file())
        def bad(req, timeout):
            return MockResponse(URL, b"<html>" + b"x" * 3000, "image/jpeg")
        with tempfile.TemporaryDirectory() as tmp:
            saved = download_exact_photo(URL, Path(tmp), 94789, opener=bad)
            self.assertEqual(saved["status"], "LOWCHART_IMAGE_CONTENT_INVALID")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_default_enrichment_uses_telegram_name_when_lowchart_option_differs(self):
        from gh_only.default_deal_automation import enrich_deal
        telegram_name = NAME.replace("블랙 + 실버, 1개", "화이트, 2개")
        deal = {
            "source_message_id": 94888,
            "source_post_url": "https://t.me/jirum/94888",
            "source_text": "☆특가 11,000원\n" + telegram_name +
                           "\nhttps://link.coupang.com/a/test",
            "title": telegram_name,
            "sale_price_krw": 11000,
            "original_purchase_url": "https://link.coupang.com/a/test",
        }
        photo = parse_item_photo(BASE_HTML, ITEM, telegram_name)
        self.assertEqual(photo["status"], "LOWCHART_EXACT_ITEM_IMAGE")
        with tempfile.TemporaryDirectory() as tmp, \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value=photo), \
             patch("gh_only.default_deal_automation.download_exact_photo",
                   return_value={"status": "IMAGE_SAVED",
                                 "saved_name": "jirum_94888_lowchart_photo.jpg"}), \
             patch("gh_only.default_deal_automation.get_kakao_og",
                   side_effect=AssertionError("No Kakao login")) as kakao:
            result = enrich_deal(
                deal, Path(tmp), resolver=lambda _: ITEM,
                history_fetch=lambda *args: {"usable_historical_reference": False},
                owner_tag="AF9668915",
            )
        self.assertEqual(result["photo"]["status"], "IMAGE_SAVED")
        self.assertTrue(result["image_product_match"])
        self.assertTrue(result["image_exact_item_page_verified"])
        self.assertFalse(result["image_same_named_option"])
        caption = result["caption"]["caption"]
        self.assertIn("상품명 : " + telegram_name, caption)
        self.assertNotIn("상품명 : " + NAME, caption)
        self.assertIn("핫딜 특가 : 11,000원", caption)
        kakao.assert_not_called()

    def test_default_enrichment_does_not_call_kakao(self):
        from gh_only.default_deal_automation import enrich_deal
        deal = {
            "source_message_id":94789,
            "source_post_url":"https://t.me/jirum/94789",
            "source_text": NAME + "\n특가 13,450원",
            "original_purchase_url":"https://link.coupang.com/a/test",
            "title":NAME,"sale_price_krw":13450,
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch("gh_only.default_deal_automation.resolve_identity",return_value=ITEM), \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value=parse_item_photo(BASE_HTML, ITEM, NAME)) as lookup, \
             patch("gh_only.default_deal_automation.get_kakao_og",
                   side_effect=AssertionError("Kakao must never be invoked")) as kakao, \
             patch("gh_only.default_deal_automation.download_exact_photo",
                   return_value={"status":"IMAGE_SAVED",
                                 "saved_name":"jirum_94789_lowchart_photo.jpg"}), \
             patch("gh_only.default_deal_automation.fetch_one",
                   return_value={"usable_historical_reference":False}):
            r = enrich_deal(deal, Path(tmp), resolver=lambda _: ITEM,
                            history_fetch=lambda *a: {"usable_historical_reference": False},
                            owner_tag="AF9668915")
        lookup.assert_called_once()
        kakao.assert_not_called()
        self.assertTrue(r["image_product_match"])
        self.assertTrue(r["image_exact_item_page_verified"])
        self.assertEqual(r["photo"]["status"], "IMAGE_SAVED")
        self.assertEqual(r["image_source"], "lowchart_exact_item_public_page")


if __name__ == "__main__":
    unittest.main()
