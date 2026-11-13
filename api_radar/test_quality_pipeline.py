"""Offline-only tests: Naver trend, exact-item verification, isolated publisher."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from api_radar import trend, quality, publisher


class TrendTests(TestCase):
    def test_curated_keywords_only(self):
        self.assertEqual(trend.matching_topic("저소음 무선 키보드 추천"),
                         ("무선 키보드", "50000003"))
        self.assertIsNone(trend.matching_topic("관계없는 무명 상품"))

    def test_official_hub_payload(self):
        now = datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)
        body = trend.trend_payload("가습기", "50000003", now)
        self.assertEqual(body["keyword"], [{"name": "가습기", "param": ["가습기"]}])
        self.assertEqual(body["timeUnit"], "date")
        self.assertEqual(body["category"], "50000003")
        self.assertEqual(len(body["startDate"]), 10)

    def test_relative_click_uptrend_and_flat(self):
        beginning = datetime(2026, 10, 15)
        def payload(vals):
            return {"results": [{"title": "가습기",
                                 "data": [{"period": (beginning + timedelta(days=i)).date().isoformat(),
                                           "ratio": x} for i, x in enumerate(vals)]}]}
        up = trend.parse_trend(payload([20.0]*7 + [30.0]*14 + [50.0]*7), "가습기")
        self.assertTrue(up["demand_uptrend"])
        self.assertTrue(up["not_absolute_search_count"])
        self.assertEqual(up["growth_ratio"], 2.5)
        self.assertFalse(trend.parse_trend(payload([25]*28), "가습기")["demand_uptrend"])

    def test_no_keys_never_attempts_network(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("api_radar.trend.request.urlopen") as opened:
            with self.assertRaisesRegex(trend.TrendError, "NAVER_HUB_KEYS_MISSING"):
                trend.fetch_topic_trend("가습기", "50000003")
            opened.assert_not_called()


class QualityTests(TestCase):
    def test_option_required_and_pid_exact(self):
        good = "https://www.coupang.com/vp/products/12345678?itemId=98765432&vendorItemId=45678901"
        self.assertEqual(quality.extract_exact_identity(good, "12345678"),
                         {"product_id": "12345678", "item_id": "98765432",
                          "vendor_item_id": "45678901"})
        self.assertIsNone(quality.extract_exact_identity(good, "22222222"))
        self.assertIsNone(quality.extract_exact_identity(
            "https://www.coupang.com/vp/products/12345678", "12345678"))
        self.assertIsNone(quality.extract_exact_identity(
            "https://evil.example/vp/products/12345678?itemId=98765432",
            "12345678"))

    def test_explicit_affsdp_api_landing(self):
        url = "https://link.coupang.com/re/AFFSDP?pageKey=12345678&itemId=98765432"
        ident = quality.extract_exact_identity(url, "12345678")
        self.assertEqual(ident["item_id"], "98765432")

    def test_same_option_history_required(self):
        product = {"id": "12345678", "name": "오뚜기 참기름 1개", "price": 30000}
        identity = {"product_id": "12345678", "item_id": "98765432"}
        def good(pid, iid, tokens, price):
            self.assertEqual((pid,iid,price), ("12345678","98765432",30000))
            return {"source_item_identifiers_present": True,
                    "title_option_matches": True,
                    "usable_historical_reference": True,
                    "visible_exact_item_prices_verified": True,
                    "observed_current_krw": 30000, "observed_average_krw": 40000,
                    "history_window": "recent_3_weeks",
                    "source_url": "https://www.lowchart.com/12345678-98765432"}
        checked = quality.check_exact_history(product, identity, fetch=good)
        self.assertEqual(checked["discount_pct"], 25.0)
        def bad(*args):
            result = good(*args)
            result["title_option_matches"] = False
            return result
        with self.assertRaisesRegex(quality.QualityError, "EXACT_OPTION_HISTORY_UNVERIFIED"):
            quality.check_exact_history(product, identity, fetch=bad)

    def test_cdn_restrictions(self):
        self.assertTrue(quality.safe_url("https://image15.coupangcdn.com/x.jpg", image=True))
        self.assertTrue(quality.safe_url("https://ads-partners.coupang.com/openapi/image/abcd1234", image=True))
        self.assertFalse(quality.safe_url("https://ads-partners.coupang.com/openapi/impression/abcd1234", image=True))
        self.assertFalse(quality.safe_url("http://image15.coupangcdn.com/x.jpg", image=True))
        self.assertFalse(quality.safe_url("https://evil.example/x.jpg", image=True))


class PublisherTests(TestCase):
    def test_missing_keys_no_publish(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("api_radar.publisher.load_state") as load:
            self.assertEqual(publisher.run(), 0)
            load.assert_not_called()

    def test_candidate_requires_fresh_and_curated_topic(self):
        t = datetime(2026, 11, 13, 5, 0, tzinfo=timezone.utc)
        state = {"products": {"12345678": {
            "name": "무선 키보드 저소음 정품", "last_price": 39900,
            "last_source": "goldbox",
            "last_product_url": "https://link.coupang.com/a/Abcd123456",
            "last_image_url": "https://image15.coupangcdn.com/x.jpg",
            "last_checked_at_utc": t.isoformat(),
        }}, "published": {}, "api_publish_claims": {}}
        found = publisher.select_candidates(state, t + timedelta(minutes=30))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["topic"][0], "무선 키보드")
        state["api_publish_claims"]["12345678"] = {"status": "review"}
        self.assertEqual(publisher.select_candidates(state, t), [])

    def test_real_goldbox_style_category_fallback_not_fake_search_data(self):
        t = datetime(2026, 11, 13, 10, 14, tzinfo=timezone.utc)
        sample = {"name": "탐사 강아지 사료 연어 레시피",
                  "category": "반려/애완용품", "last_price": 30990,
                  "last_source": "goldbox",
                  "last_product_url": "https://link.coupang.com/re/AFFSDP?pageKey=12345678&itemId=98765432",
                  "last_image_url": "https://image15.coupangcdn.com/x.jpg",
                  "last_checked_at_utc": t.isoformat()}
        state = {"products": {"12345678": sample}, "published": {},
                 "api_publish_claims": {}}
        picked = publisher.select_candidates(state, t + timedelta(minutes=5))
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["topic"], ("반려/애완용품", None))
        self.assertTrue(picked[0]["category_fallback"])
        self.assertEqual(publisher.select_candidates(state, t, trend_mode="hub"), [])
        sample["category"] = "국내투어"
        self.assertEqual(publisher.select_candidates(state, t), [])

    def test_curated_mode_checks_next_candidate_when_price_unverified(self):
        t = datetime(2026, 11, 13, 10, 14, tzinfo=timezone.utc)
        def product(pid, category, name):
            return {"name": name, "category": category,
                    "last_price": 30000, "last_source": "goldbox",
                    "last_product_url": "https://link.coupang.com/re/AFFSDP?pageKey="
                                       + pid + "&itemId=98765432",
                    "last_image_url": "https://image15.coupangcdn.com/x.jpg",
                    "last_checked_at_utc": t.isoformat()}
        state = {"products": {
            "12345678": product("12345678", "생활용품", "샤워기헤드 필터 수압"),
            "22345678": product("22345678", "식품", "코카콜라 오리지널 무라벨")},
            "published": {}, "api_publish_claims": {}}
        env = {"API_RADAR_PUBLISH_ENABLED": "true",
               "API_RADAR_TREND_MODE": "curated",
               "NAVER_BLOG_STORAGE_STATE_JSON": "fake",
               "COUPANG_PARTNERS_ACCESS_KEY": "fake",
               "COUPANG_PARTNERS_SECRET_KEY": "fake"}
        good = {"exact_product_item_verified": True, "reference_price": 40000,
                "discount_pct": 25, "history_window": "recent_3_weeks"}
        with patch.dict(os.environ, env, clear=True), \
             patch("api_radar.publisher.load_state", return_value=(state, "sha")), \
             patch("api_radar.publisher.resolve_exact_identity",
                   return_value={"product_id": "12345678", "item_id": "98765432"}) as resolve, \
             patch("api_radar.publisher.check_exact_history",
                   side_effect=[quality.QualityError("EXACT_OPTION_HISTORY_UNVERIFIED"), good]) as check, \
             patch("api_radar.publisher.download_verified_image") as download:
            self.assertEqual(publisher.run(preview=True, now=t + timedelta(minutes=5)), 0)
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual(check.call_count, 2)
            download.assert_not_called()

    def test_claim_before_submit_and_checkpoint(self):
        now = datetime(2026, 11, 13, 5, tzinfo=timezone.utc)
        source = {"price": 30000, "checked_at": now.isoformat()}
        state = {"products": {"12345678": {
            "last_price": 30000, "last_checked_at_utc": now.isoformat()
        }}, "published": {}}
        publisher.claim(state, "12345678", source, now)
        self.assertEqual(state["api_publish_claims"]["12345678"]["source_price"], 30000)
        with self.assertRaisesRegex(Exception, "RADAR_CLAIM_ALREADY_EXISTS"):
            publisher.claim(state, "12345678", source, now)
        publisher.checkpoint(state, "12345678",
                             "https://blog.naver.com/mw5114/111111111", now)
        self.assertIn("12345678", state["published"])
        self.assertNotIn("12345678", state["api_publish_claims"])

    def test_article_without_naver_hub_does_not_claim_clicks(self):
        with tempfile.TemporaryDirectory() as td:
            photo = Path(td) / "photo.jpg"
            photo.write_bytes(b"\xff\xd8\xff" + b"0" * 1200)
            product = {"name": "오뚜기 참기름 1개", "price": 30000}
            history = {"exact_product_item_verified": True,
                       "reference_price": 40000, "discount_pct": 25.0,
                       "history_window": "recent_3_weeks"}
            article = publisher.compose_article(
                product, history, None, photo,
                "https://link.coupang.com/a/Abcd12345")
            self.assertIn("쿠팡 파트너스 활동", article["body"])
            self.assertNotIn("클릭지수", article["body"])
            self.assertNotIn("검색량", article["body"])
            self.assertNotIn("실사용 후기", article["body"])

    def test_curated_mode_does_not_require_naver_hub_keys(self):
        with patch.dict(os.environ, {"API_RADAR_PUBLISH_ENABLED": "true",
                                     "API_RADAR_TREND_MODE": "curated",
                                     "NAVER_BLOG_STORAGE_STATE_JSON": "fake-session",
                                     "COUPANG_PARTNERS_ACCESS_KEY": "fake",
                                     "COUPANG_PARTNERS_SECRET_KEY": "fake"},
                        clear=True), patch("api_radar.publisher.load_state",
                        return_value=({"products": {}, "published": {}}, None)):
            self.assertEqual(publisher.run(), 0)

    def test_article_contains_disclosure_and_not_false_review(self):
        with tempfile.TemporaryDirectory() as td:
            file = Path(td) / "x.jpg"
            file.write_bytes(b"\xff\xd8\xff" + b"0"*1200)
            product = {"name": "오뚜기 참기름 1개", "price": 30000}
            history = {"exact_product_item_verified": True,
                       "reference_price": 40000, "discount_pct": 25.0,
                       "history_window": "recent_3_weeks"}
            signal = {"verified": True, "demand_uptrend": True,
                      "keyword": "참기름", "recent_seven_day_mean": 40.0}
            article = publisher.compose_article(
                product, history, signal, file,
                "https://link.coupang.com/a/Abcd12345")
            self.assertTrue(article["title"].startswith("[쿠팡🔥핫딜]"))
            self.assertIn("쿠팡 파트너스 활동", article["body"])
            self.assertIn("공식 정가가 아닙니다", article["body"])
            self.assertNotIn("실사용 후기", article["body"])
            self.assertNotIn("https://link.coupang.com", article["body"])


if __name__ == "__main__":
    main()
