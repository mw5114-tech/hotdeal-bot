"""Kakao image search offline fixture tests - no real key or HTTP."""
import json
import unittest
from gh_only.kakao_image_search import (
    query_terms, url_product_identity, normalize_search_results, safe_https,
    find_image_candidates,
)

PRODUCT = "6221369038"
ITEM = "12453073752"
EXACT = "https://www.coupang.com/vp/products/6221369038?itemId=12453073752"
OTHER = "https://www.coupang.com/vp/products/6221369038?itemId=18037609983"
IMAGE = "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/test.jpg"


def fake_payload(*docs):
    return {"meta": {"total_count": len(docs), "is_end": True},
            "documents": list(docs)}


def item(url, img=IMAGE):
    return {"doc_url": url, "image_url": img,
            "thumbnail_url": "https://search1.kakaocdn.net/argon/test",
            "display_sitename": "쿠팡", "width": 1000, "height": 1000}


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
    def geturl(self):
        return "https://dapi.kakao.com/v2/search/image"
    def read(self, length):
        return self.payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class ImageAPITests(unittest.TestCase):
    def test_exact_id_and_item_match_only(self):
        result = normalize_search_results(
            fake_payload(item(EXACT), item(OTHER)), PRODUCT, ITEM)
        self.assertEqual(len(result["exact_source_url_candidates"]), 1)
        self.assertEqual(len(result["other_candidates"]), 1)
        self.assertEqual(result["other_candidates"][0]["source_match"],
                         "SOURCE_URL_PRODUCT_ONLY_OR_DIFFERENT_ITEM")
        self.assertFalse(result["safe_to_autopublish"])

    def test_product_only_is_not_exact(self):
        u = "https://www.coupang.com/vp/products/6221369038"
        self.assertEqual(url_product_identity(u, PRODUCT, ITEM),
                         "SOURCE_URL_PRODUCT_ONLY_OR_DIFFERENT_ITEM")

    def test_other_shop_same_label_not_exact(self):
        u = "https://www.other-shop.example/product/6221369038?itemId=12453073752"
        self.assertEqual(url_product_identity(u, PRODUCT, ITEM), "OTHER_SITE")

    def test_bad_image_scheme_ignored(self):
        result = normalize_search_results(
            fake_payload(item(EXACT, img="http://insecure.example/a.jpg")), PRODUCT, ITEM)
        self.assertEqual(len(result["exact_source_url_candidates"]), 0)

    def test_keyword_search_fallback_when_no_exact(self):
        called = []
        def fake_open(req, timeout):
            self.assertEqual(req.get_header("Authorization"), "KakaoAK test-key-1234")
            called.append(req.full_url)
            return FakeResponse(fake_payload(item("https://shop.example/a.png")))
        report = find_image_candidates(PRODUCT, ITEM, "오뚜기 핫케이크 믹스 1kg 1개",
                                       "test-key-1234", opener=fake_open)
        self.assertEqual(report["requests_attempted"], 2)
        self.assertEqual(len(called), 2)
        self.assertFalse(report["publish_ready"])

    def test_stop_after_exact_url(self):
        called = []
        def fake_open(req, timeout):
            called.append(1)
            return FakeResponse(fake_payload(item(EXACT)))
        report = find_image_candidates(PRODUCT, ITEM, "오뚜기 핫케이크 믹스 1kg 1개",
                                       "test-key-1234", opener=fake_open)
        self.assertEqual(report["requests_attempted"], 1)
        self.assertEqual(len(called), 1)
        self.assertFalse(report["exact_option_image_verified"])

    def test_missing_key_no_request(self):
        with self.assertRaises(ValueError):
            find_image_candidates(PRODUCT, ITEM, "오뚜기 핫케이크 믹스 1kg 1개", "")

    def test_non_https_rejected(self):
        self.assertFalse(safe_https("https://evil.com@other.com/"))
        self.assertFalse(safe_https("http://www.coupang.com/a.jpg"))
        self.assertFalse(safe_https("https://www.coupang.com:444/a.jpg"))

    def test_ids_required(self):
        with self.assertRaises(ValueError):
            query_terms("123", ITEM, "오뚜기 핫케이크 믹스")

    def test_payload_schema_validation(self):
        with self.assertRaises(ValueError):
            normalize_search_results({"documents": []}, PRODUCT, ITEM)


if __name__ == "__main__":
    unittest.main()
