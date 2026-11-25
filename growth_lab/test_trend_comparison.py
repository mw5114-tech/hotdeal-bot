"""Offline tests: no credential, browser, Naver or Coupang network use."""
import contextlib
from email.message import Message
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
from growth_lab.trend_comparison import (
    shop_payload, score_group, choose_pair, size_tokens, pair_key, run, compose,
)

from api_radar.collector import normalize
from growth_lab.comparison_photos import choose_photo, image_bytes, shopping_photos, title_match

PHOTO = "https://shopping-phinf.pstatic.net/main_1234567/12345678901.20250101120000.jpg?type=f300"
CARD = "https://cr3.shopping.naver.com/v2/bridge/searchGate?nv_mid=12345678901"
NAME = "삼성전자 외장 SSD T7 1TB"


def response(req, content, mime, url=None):
    result = io.BytesIO(content)
    result.status = 200
    result.geturl = lambda: url or req.full_url
    result.headers = Message()
    result.headers["content-type"] = mime
    return result


class ComparisonTests(unittest.TestCase):
    def test_payload_is_last_28_complete_days(self):
        now = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
        payload = shop_payload("50000003", ("가습기", "무선 키보드"), now)
        self.assertEqual(payload["endDate"], "2026-11-22")
        self.assertEqual(payload["startDate"], "2026-10-26")
        self.assertEqual(len(payload["keyword"]), 2)

    def test_group_scores_only_verified_relative_indices(self):
        def daily(v, offset):
            from datetime import date, timedelta
            return [{"period": str(date(2026, 10, 26) + timedelta(days=i)),
                     "ratio": v if i < 21 else v + offset}
                    for i in range(28)]
        data = {"results": [
            {"title": "가습기", "data": daily(40, 20)},
            {"title": "보조배터리", "data": daily(20, 0)},
            {"title": "스마트폰", "data": daily(90, 10)},
        ]}
        ranks = score_group(data, ("가습기", "보조배터리"), "IT/가전")
        self.assertEqual([x["keyword"] for x in ranks], ["가습기"])
        self.assertTrue(ranks[0]["relative_index_only"])
        self.assertGreater(ranks[0]["growth_ratio"], 1)

    def test_missing_history_never_promoted(self):
        rows = {"results": [{"title": "가습기",
                            "data": [{"period": "2026-11-22", "ratio": 100}]}]}
        self.assertEqual(score_group(rows, ("가습기",), "IT/가전"), [])

    def test_pair_rejects_different_option_size_and_image(self):
        def offer(pid, name, price, image):
            return {"id": pid, "name": name, "price": price, "image": image}
        offers = [
            offer("11111", "가습기 기본 2L", 20000, "image1"),
            offer("22222", "가습기 기본 4L", 25000, "image2"),
            offer("33333", "가습기 다른회사 2L", 28000, "image3"),
        ]
        self.assertEqual([x["id"] for x in choose_pair(offers)], ["11111", "33333"])
        self.assertIsNone(choose_pair(offers[:2]))

    def test_size_and_key(self):
        self.assertIn("512GB", size_tokens("외장 SSD 512GB"))
        first = {"id": "11111"}
        second = {"id": "22222"}
        self.assertEqual(pair_key("SSD", first, second), pair_key("SSD", second, first))


    def test_review_style_recommends_by_price_without_claiming_experience(self):
        now = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
        a = {"id": "11111", "name": "가습기 첫 제품 2L", "price": 30000,
             "is_rocket": True, "free_shipping": False}
        b = {"id": "22222", "name": "가습기 두 번째 2L", "price": 20000,
             "is_rocket": False, "free_shipping": True}
        links = ("https://link.coupang.com/a/first", "https://link.coupang.com/a/second")
        with patch("growth_lab.trend_comparison.issued_link_url", return_value=True):
            article = compose({"keyword": "가습기"}, a, b, links, Path("photo.jpg"), now)
            tied = compose({"keyword": "가습기"}, a, dict(b, price=30000),
                           links, Path("photo.jpg"), now)
        body = "\n".join(article["paragraphs"])
        self.assertIn("직접 구매하거나 사용한 체험기는 아닙니다", body)
        self.assertIn("저는 " + b["name"], body)
        self.assertIn("10,000원 저렴하게", body)
        self.assertIn("물통 용량", body)
        self.assertNotIn("검색 API", body)
        self.assertEqual(article["title"], "침실·책상용 가습기 고르는 법, 추천 제품 2가지")
        self.assertTrue(article["paragraphs"][0].startswith("난방을 틀고"))
        self.assertIn("2L 규격 또는 구성", body)
        self.assertTrue(7 <= len(article["paragraphs"]) + 1 <= 15)
        self.assertTrue(all(10 <= len(p) <= 900 for p in article["paragraphs"]))
        self.assertEqual([item["url"] for item in article["links"]], list(links))
        self.assertIn(a["name"], article["links"][0]["label"])
        self.assertIn(b["name"], article["links"][1]["label"])
        self.assertIn("표시가격은 같습니다", "\n".join(tied["paragraphs"]))


class ComparisonPhotoTests(unittest.TestCase):
    def test_exact_name_retains_option_model_and_bundle(self):
        self.assertTrue(title_match(NAME, "삼성전자   외장 SSD T7, 1TB"))
        for a, b in [(NAME, "삼성전자 외장 SSD T7 2TB"),
                     (NAME, NAME + " 블랙"), (NAME + " - 블랙", NAME + " - 블루"),
                     ("통세척 가습기 2.5L X1", "통세척 가습기 25L X1"),
                     (NAME + " 1개", NAME + " 2개")]:
            self.assertFalse(title_match(a, b), (a, b))

    def test_public_search_extracts_only_exact_shopping_cards_on_known_cdn(self):
        raw = f"""
          <img src="{PHOTO}" alt="{NAME}">
          <a href="{CARD}"><img src="{PHOTO}" alt="{NAME} 2개"></a>
          <a href="https://example.org/"><img src="{PHOTO}" alt="{NAME}"></a>
          <a href="{CARD}"><img src="https://shopping-phinf.pstatic.net.example.org/product.jpg" alt="{NAME}"></a>
          <a href="{CARD}"><img src="{PHOTO}" alt="{NAME}"></a>
          <a href="{CARD}"><img src="{PHOTO}" alt="{NAME}"></a>
        """.encode()
        def open_search(req, *, timeout):
            self.assertTrue(req.full_url.startswith("https://m.search.naver.com/search.naver?query="))
            return response(req, raw, "text/html")
        self.assertEqual(shopping_photos(NAME, opener=open_search),
                         {"status": "SHOPPING_MATCH", "images": [PHOTO]})

    def test_shorter_query_does_not_relax_model_or_option_check(self):
        name = "쿠쿠전자 가습기 CH-BS302FW 3.8L"
        queries = []
        def open_search(req, *, timeout):
            query = parse_qs(urlsplit(req.full_url).query)["query"][0]
            queries.append(query)
            title = name if query == "CH-BS302FW" else name.replace("3.8L", "3L")
            raw = f'<a href="{CARD}"><img src="{PHOTO}" alt="{title}"></a>'.encode()
            return response(req, raw, "text/html")
        self.assertEqual(shopping_photos(name, opener=open_search)["images"], [PHOTO])
        self.assertEqual(queries, [name, "CH-BS302FW"])

    def test_blocked_page_and_bad_image_fail_closed(self):
        def blocked(req, *, timeout):
            raise HTTPError(req.full_url, 403, "", {}, None)
        self.assertEqual(shopping_photos(NAME, opener=blocked),
                         {"status": "SHOPPING_HTTP_403", "images": []})
        self.assertEqual(image_bytes(
            PHOTO, opener=lambda req, **_: response(req, b"<html>" * 300, "image/jpeg")
        )["status"], "SHOPPING_IMAGE_INVALID_BYTES")
        self.assertEqual(image_bytes(
            PHOTO, opener=lambda req, **_: response(req, b"x", "image/jpeg", "https://example.org/a")
        )["status"], "SHOPPING_IMAGE_DOWNLOAD_UNEXPECTED")

    def test_lowchart_on_either_product_wins_before_naver_shopping(self):
        with patch("growth_lab.comparison_photos.lowchart_photo", side_effect=[
            {"status": "NO_LOWCHART"}, {"status": "IMAGE_SAVED", "source": "lowchart_exact_item_public_page"}
        ]), patch("growth_lab.comparison_photos.shopping_photos") as shopping:
            photo = choose_photo({"id": "11111"}, {"id": "22222"}, {}, Path("."))
        self.assertEqual(photo["label"], "B")
        shopping.assert_not_called()

    def test_fallback_saves_shopping_photo_with_no_coupang_photo(self):
        with TemporaryDirectory() as tmp, patch(
            "growth_lab.comparison_photos.lowchart_photo", return_value={"status": "NO_LOWCHART"}
        ), patch("growth_lab.comparison_photos.shopping_photos", return_value={
            "status": "SHOPPING_MATCH", "images": [PHOTO]
        }), patch("growth_lab.comparison_photos.image_bytes", return_value={
            "status": "SHOPPING_IMAGE_OK", "raw": b"photo", "mime": "image/jpeg"
        }):
            photo = choose_photo({"id": "11111", "name": NAME}, {"id": "22222"}, {}, Path(tmp))
            self.assertEqual(photo["source"], "naver_shopping_product_card")
            self.assertEqual(photo["path"].read_bytes(), b"photo")
            self.assertEqual(photo["path"].suffix, ".jpg")

    def test_no_matching_image_never_returns_publishable_photo(self):
        with patch("growth_lab.comparison_photos.lowchart_photo", return_value={"status": "NO_LOWCHART"}), patch(
            "growth_lab.comparison_photos.shopping_photos", return_value={"status": "SHOPPING_NO_EXACT_CARD", "images": []}
        ):
            self.assertEqual(choose_photo({"id": "11111", "name": NAME},
                                          {"id": "22222", "name": "다른 상품"}, {}, Path(".")),
                             {"status": "NO_MATCHING_PRODUCT_IMAGE"})

    def test_image_api_fallback_after_both_lowchart_fail(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {
            "TREND_COMPARISON_NAVER_IMAGE_API_ENABLED": "true"
        }), patch("growth_lab.comparison_photos.lowchart_photo",
                  return_value={"status": "HTTP_403"}), patch(
            "growth_lab.comparison_photos.shopping_photos"
        ) as web, patch("gh_only.naver_api_image_photo.fetch_matched_photo") as api:
            path = Path(tmp) / "jirum_11111_naver_api_photo.jpg"
            path.write_bytes(b"x" * 1500)
            api.return_value = {"status": "IMAGE_SAVED",
                                "saved_name": path.name,
                                "representative_naver_api_image": True,
                                "image_provider_title": NAME}
            result = choose_photo({"id": "11111", "name": NAME},
                                  {"id": "22222", "name": NAME}, {}, Path(tmp))
            self.assertEqual(result["source"], "naver_api_matched_image")
            self.assertEqual(result["path"], path)
            api.assert_called_once()
            web.assert_not_called()

    def test_image_api_wrong_option_does_not_pass(self):
        with patch.dict("os.environ", {"TREND_COMPARISON_NAVER_IMAGE_API_ENABLED": "true"}), \
             patch("growth_lab.comparison_photos.lowchart_photo",
                   return_value={"status": "HTTP_403"}), \
             patch("gh_only.naver_api_image_photo.fetch_matched_photo", return_value={
                 "status": "IMAGE_SAVED", "representative_naver_api_image": True,
                 "image_provider_title": NAME.replace("1TB", "2TB")
             }), patch("growth_lab.comparison_photos.shopping_photos",
                       return_value={"status": "SHOPPING_NO_EXACT_CARD", "images": []}):
            self.assertEqual(choose_photo({"id": "11111", "name": NAME},
                                          {"id": "22222", "name": NAME}, {}, Path(".")),
                             {"status": "NO_MATCHING_PRODUCT_IMAGE"})

    def test_missing_photo_is_safe_defer_without_claim_or_link(self):
        topic = {"keyword": "외장 SSD", "category_name": "가전"}
        offers = [{"id": pid, "name": NAME, "price": 29900, "image": None}
                  for pid in ("11111", "22222")]
        with patch.dict("os.environ", {"TREND_COMPARISON_PUBLISH_ENABLED": "true"}), \
             patch("growth_lab.trend_comparison.load", return_value=(None, None)), \
             patch("growth_lab.trend_comparison.load_state", return_value=({}, None)), \
             patch("growth_lab.trend_comparison.cooldown_remaining_seconds", return_value=0), \
             patch("growth_lab.trend_comparison.ranked_topics", return_value=[topic]), \
             patch("growth_lab.trend_comparison.get_api"), \
             patch("growth_lab.trend_comparison.extract_rows", return_value=[]), \
             patch("growth_lab.trend_comparison.products_for_comparison", return_value=offers), \
             patch("growth_lab.trend_comparison.choose_photo",
                   return_value={"status": "NO_MATCHING_PRODUCT_IMAGE"}), \
             patch("growth_lab.trend_comparison.save") as save, \
             patch("growth_lab.trend_comparison.tracked_link") as link, \
             contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(run(preview=False), 0)
        self.assertIn("TREND_COMPARISON_NO_APPROVED_IMAGE", log.getvalue())
        save.assert_not_called()
        link.assert_not_called()

    def test_missing_coupang_image_is_allowed_only_on_opted_in_path(self):
        row = {"productId": 12345, "productName": NAME, "productPrice": 19900,
               "productUrl": "https://www.coupang.com/vp/products/12345"}
        self.assertIsNone(normalize(row, "search"))
        self.assertIsNone(normalize(row, "search", require_image=False)["image"])
        offers = [normalize({**row, "productId": pid}, "search", require_image=False)
                  for pid in (12345, 67890)]
        self.assertIsNotNone(choose_pair(offers))

    def test_workflow_entry_preview_uses_fallback_but_never_writes_or_publishes(self):
        topic = {"keyword": "외장 SSD", "category_name": "가전"}
        offers = [{"id": pid, "name": NAME, "price": 29900, "image": None}
                  for pid in ("11111", "22222")]
        with patch("growth_lab.trend_comparison.load", return_value=(None, None)), patch(
            "growth_lab.trend_comparison.load_state", return_value=({}, None)
        ), patch("growth_lab.trend_comparison.ranked_topics", return_value=[topic]), patch(
            "growth_lab.trend_comparison.get_api"
        ), patch("growth_lab.trend_comparison.extract_rows", return_value=[]), patch(
            "growth_lab.trend_comparison.products_for_comparison", return_value=offers
        ) as candidates, patch(
            "growth_lab.trend_comparison.choose_photo", return_value={
                "status": "IMAGE_SAVED", "source": "naver_shopping_product_card"
            }
        ) as select, patch("growth_lab.trend_comparison.save") as save, patch(
            "growth_lab.trend_comparison.tracked_link"
        ) as link, contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(run(preview=True), 0)
        candidates.assert_called_once_with([], topic["keyword"], require_image=False)
        select.assert_called_once()
        save.assert_not_called()
        link.assert_not_called()
        self.assertIn("PREVIEW_ONLY posts=0 image_ready=true", log.getvalue())


if __name__ == "__main__":
    unittest.main()
