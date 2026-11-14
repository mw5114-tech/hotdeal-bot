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

    def test_verified_goldbox_identity_can_issue_real_api_contract_without_naver(self):
        """Use ACTUAL quality resolver and ACTUAL deeplink builder; mock only HTTP."""
        import json
        from gh_only.coupang_partners_api import create_product_deeplink
        from gh_only.direct_product_links import regular_product_url
        source = {"id": "12345678",
                  "url": "https://link.coupang.com/re/AFFSDP?"
                         "pageKey=12345678&itemId=98765432&vendorItemId=45678901"}
        exact = quality.resolve_exact_identity(source)
        self.assertEqual(exact["status"], "EXACT_ITEM_IDENTIFIED")
        canonical = ("https://www.coupang.com/vp/products/12345678?"
                     "itemId=98765432&vendorItemId=45678901")
        self.assertEqual(regular_product_url(exact), canonical)
        class ApiResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self, n):
                return json.dumps({"rCode": "0", "data": [{
                    "originalUrl": canonical,
                    "shortenUrl": "https://link.coupang.com/a/AbcDef12345",
                    "landingUrl": ("https://link.coupang.com/re/AFFSDP?"
                                   "pageKey=12345678&itemId=98765432&subid=naverblog")
                }]}).encode()[:n]
        captured = {}
        def fake_open(req, timeout):
            captured["body"] = json.loads(req.data)
            return ApiResponse()
        with patch.dict(os.environ, {
            "COUPANG_PARTNERS_ACCESS_KEY": "testaccess",
            "COUPANG_PARTNERS_SECRET_KEY": "testsecret",
        }), patch("gh_only.coupang_partners_api.request.urlopen",
                  side_effect=fake_open) as net:
            issued = create_product_deeplink(exact, subid="naverblog")
        self.assertEqual(net.call_count, 1)
        self.assertEqual(captured["body"],
                         {"coupangUrls": [canonical], "subId": "naverblog"})
        self.assertEqual(issued["partners_link"],
                         "https://link.coupang.com/a/AbcDef12345")
        self.assertTrue(issued["official_link_issued"])

    def test_unverified_url_cannot_gain_deeplink_identity(self):
        from gh_only.coupang_partners_api import create_product_deeplink, CoupangPartnersError
        for bad in ("https://link.coupang.com/a/AbcDef12345",
                    "https://www.coupang.com/vp/products/12345678",
                    "https://www.coupang.com/vp/products/99999999?itemId=98765432"):
            with self.subTest(url=bad):
                with self.assertRaises(quality.QualityError):
                    quality.resolve_exact_identity({"id":"12345678", "url":bad})
        with patch("gh_only.coupang_partners_api.request.urlopen") as net:
            with self.assertRaises(CoupangPartnersError):
                create_product_deeplink({"product_id":"12345678",
                                         "item_id":"98765432"}, subid="naverblog")
        net.assert_not_called()

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
        self.assertTrue(quality.safe_url("https://ads-partners.coupang.com/image1/abcd1234.jpg==", image=True))
        self.assertIs(type(quality.safe_url("https://ads-partners.coupang.com/image1/abcd1234.jpg==", image=True)), bool)
        self.assertTrue(quality.safe_url("https://ads-partners.coupang.com/300w_300h/abcd1234.jpg", image=True))
        self.assertFalse(quality.safe_url("https://ads-partners.coupang.com/openapi/image/abcd1234", image=True))
        self.assertFalse(quality.safe_url("https://ads-partners.coupang.com/t/p", image=True))
        self.assertFalse(quality.safe_url("https://ads-partners.coupang.com/300w_300h/abcd1234.jpg?token=opaque", image=True))
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
        state["api_publish_claims"]["12345678"] = {"status": "review", "claimed_at": t.isoformat()}
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
        self.assertEqual(len(publisher.select_candidates(state, t, trend_mode="hub")), 1)
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
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(check.call_count, 1)
            download.assert_not_called()

    def test_daily_limit_counts_ambiguous_claims_and_kst_midnight(self):
        t = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
        state = {"products": {}, "published": {
            "11111111": {"kst_date": "2026-11-14"},
            "22222222": {"kst_date": "2026-11-14"}},
            "api_publish_claims": {
                "33333333": {"claimed_at": "2026-11-14T00:59:59+09:00"}}}
        self.assertEqual(publisher.daily_slot_count(state, t), 3)
        self.assertEqual(publisher.select_candidates(state, t), [])
        state["api_publish_claims"]["33333333"]["claimed_at"] = "2026-11-13T23:59:59+09:00"
        self.assertEqual(publisher.daily_slot_count(state, t), 2)
        state["api_publish_claims"]["33333333"]["claimed_at"] = "unparseable"
        with self.assertRaisesRegex(Exception, "RADAR_CLAIM_DATE_INVALID"):
            publisher.daily_slot_count(state, t)

    def test_five_posts_release_one_per_fifteen_minutes_without_daily_quota(self):
        t = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
        products = {}
        for n in range(5):
            pid = str(12345678 + n)
            products[pid] = {
                "name": "생활용품 필터 샤워기 " + str(n),
                "category": "생활용품", "last_price": 30000,
                "last_source": "goldbox",
                "last_product_url": "https://link.coupang.com/re/AFFSDP?pageKey="
                                    + pid + "&itemId=98765432",
                "last_image_url": "https://image15.coupangcdn.com/a.jpg",
                "last_checked_at_utc": t.isoformat()}
        state = {"products": products, "published": {}, "api_publish_claims": {}}
        env = {"API_RADAR_PUBLISH_ENABLED": "true",
               "API_RADAR_TREND_MODE": "curated",
               "NAVER_BLOG_STORAGE_STATE_JSON": "fake",
               "COUPANG_PARTNERS_ACCESS_KEY": "fake",
               "COUPANG_PARTNERS_SECRET_KEY": "fake"}
        history = {"exact_product_item_verified": True, "reference_price": 45000,
                   "discount_pct": 33.3, "history_window": "recent_3_weeks"}
        def identity(p):
            return {"product_id": p["id"], "item_id": "98765432",
                    "vendor_item_id": None}
        def fake_url(*a, **kw):
            return "https://link.coupang.com/a/TestOfficial123"
        def publish(*a, **kw):
            publish.count += 1
            return "https://blog.naver.com/mw5114/" + str(100000000 + publish.count)
        publish.count = 0
        clock = {"now": t}
        with patch.dict(os.environ, env, clear=True), \
             patch("api_radar.publisher.utc_now", side_effect=lambda:clock["now"]), \
             patch("api_radar.publisher.load_state", side_effect=lambda: (state, "sha")), \
             patch("api_radar.publisher.save_state") as save, \
             patch("api_radar.publisher.resolve_exact_identity", side_effect=identity), \
             patch("api_radar.publisher.check_exact_history", return_value=history), \
             patch("api_radar.publisher.download_verified_image",
                   return_value=Path("/tmp/placeholder.jpg")), \
             patch("gh_only.coupang_partners_api.create_product_deeplink",
                   side_effect=lambda *a, **kw: {"partners_link":fake_url()}), \
             patch("api_radar.publisher.compose_article", return_value={"title":"test"}), \
             patch("gh_only.naver_blog_deal_publisher.submit_one", side_effect=publish):
            for expected in range(1, 6):
                self.assertEqual(publisher.run(now=clock["now"]), 0)
                self.assertEqual(len(state["published"]), expected)
                self.assertEqual(publish.count, expected)
                # Same timestamp cannot cause a second immediate publication.
                self.assertEqual(publisher.run(now=clock["now"]), 0)
                self.assertEqual(publish.count, expected)
                clock["now"] += timedelta(minutes=15)
            self.assertEqual(publisher.run(now=clock["now"]), 0)
            self.assertEqual(len(state["api_publish_claims"]), 0)
            self.assertEqual(save.call_count, 10)

    def test_cooldown_claims_and_receipts_enforce_fifteen_minute_minimum(self):
        t = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
        state = {"published":{"11111111":{"at":t.isoformat()}},
                 "api_publish_claims":{}}
        self.assertEqual(publisher.cooldown_remaining_seconds(state, t), 900)
        self.assertEqual(publisher.cooldown_remaining_seconds(
            state, t+timedelta(minutes=14,seconds=59)), 1)
        self.assertEqual(publisher.cooldown_remaining_seconds(
            state, t+timedelta(minutes=15)), 0)
        state["api_publish_claims"]["22222222"] = {
            "claimed_at":(t+timedelta(minutes=10)).isoformat()}
        self.assertEqual(publisher.cooldown_remaining_seconds(
            state, t+timedelta(minutes=15)), 600)
        self.assertEqual(publisher.cooldown_remaining_seconds(
            state, t+timedelta(minutes=25)), 0)

    def test_goldbox_b_article_never_invents_discount(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "goldbox.jpg"
            image.write_bytes(b"\xff\xd8\xff" + b"0" * 1200)
            p = {"name": "와우회원 골드박스 유기농 생수 24개",
                 "price": 10900, "category": "식품", "source": "goldbox"}
            article = publisher.compose_goldbox_article(
                p, image, "https://link.coupang.com/a/Abcd12345")
            self.assertEqual(article["grade"], "B")
            self.assertTrue(article["title"].startswith("[쿠팡🎁골드박스] "))
            self.assertIn("수집 당시 쿠팡 판매가", article["body"])
            self.assertIn("과거 평균가격 대비 할인율은 검증되지 않아 표시하지 않습니다", article["body"])
            self.assertIn("쿠팡 파트너스 활동", article["body"])
            self.assertNotIn("% 할인", article["body"])
            self.assertNotIn("평소 판매가", article["body"])
            self.assertNotIn("https://link.coupang.com/", article["body"])
            self.assertEqual(len(article["blocks"]), 9)
            long_title = publisher.goldbox_title("대용량 생수 한정상품 " * 15)
            self.assertLessEqual(len(long_title.encode("utf-16-le")) // 2, 88)
            p["source"] = "search"
            with self.assertRaisesRegex(Exception, "GOLDBOX_ARTICLE_GATES_UNVERIFIED"):
                publisher.compose_goldbox_article(p, image,
                    "https://link.coupang.com/a/Abcd12345")

    def test_a_price_verification_failure_falls_back_to_official_b(self):
        t = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
        def product(pid, source, price, category, name):
            return {"name": name, "category": category, "last_price": price,
                    "last_source": source,
                    "last_product_url": "https://link.coupang.com/re/AFFSDP?pageKey="
                                      + pid + "&itemId=98765432",
                    "last_image_url": "https://image15.coupangcdn.com/a.jpg",
                    "last_checked_at_utc": t.isoformat()}
        state = {"products": {
            "12345678": product("12345678", "search", 30000, "가전디지털",
                                "무선 키보드 저소음 블루투스"),
            "22345678": product("22345678", "goldbox", 10000, "식품",
                                "유기농 생수 24개 골드박스"),
        }, "published": {}, "api_publish_claims": {}}
        env = {"API_RADAR_PUBLISH_ENABLED": "true", "API_RADAR_TREND_MODE": "curated",
               "NAVER_BLOG_STORAGE_STATE_JSON": "fake-session",
               "COUPANG_PARTNERS_ACCESS_KEY": "fake",
               "COUPANG_PARTNERS_SECRET_KEY": "fake"}
        written = []
        def submit(article, *_args, **_kw):
            written.append(article)
            return "https://blog.naver.com/mw5114/100000001"
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "test.jpg"
            image.write_bytes(b"\xff\xd8\xff" + b"0" * 1200)
            with patch.dict(os.environ, env, clear=True), \
                 patch("api_radar.publisher.utc_now", return_value=t), \
                 patch("api_radar.publisher.load_state", side_effect=lambda:(state,"sha")), \
                 patch("api_radar.publisher.save_state"), \
                 patch("api_radar.publisher.resolve_exact_identity",
                       side_effect=lambda p: {"product_id":p["id"],"item_id":"98765432"}), \
                 patch("api_radar.publisher.check_exact_history",
                       side_effect=quality.QualityError("DISCOUNT_BELOW_THRESHOLD_OR_IMPLAUSIBLE")) as history, \
                 patch("api_radar.publisher.download_verified_image", return_value=image), \
                 patch("gh_only.coupang_partners_api.create_product_deeplink",
                       return_value={"partners_link":"https://link.coupang.com/a/Abcd12345"}), \
                 patch("gh_only.naver_blog_deal_publisher.submit_one", side_effect=submit):
                self.assertEqual(publisher.run(now=t), 0)
                self.assertEqual(history.call_count, 1)
                self.assertEqual(len(written), 1)
                self.assertEqual(written[0]["grade"], "B")
                self.assertEqual(state["published"]["22345678"]["grade"], "B")
                self.assertNotIn("12345678", state["published"])

    def test_a_then_b_on_separate_fifteen_minute_slots(self):
        t = datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)
        def p(pid, source, name):
            return {"name": name, "category": "생활용품", "last_price": 30000,
                    "last_source": source,
                    "last_product_url":"https://link.coupang.com/re/AFFSDP?pageKey="
                                       + pid + "&itemId=98765432",
                    "last_image_url":"https://image15.coupangcdn.com/a.jpg",
                    "last_checked_at_utc":t.isoformat()}
        state={"products":{
            "12345678":p("12345678", "goldbox", "샤워기 헤드 필터"),
            "22345678":p("22345678", "goldbox", "세제 캡슐 30개")},
            "published":{}, "api_publish_claims":{}}
        env={"API_RADAR_PUBLISH_ENABLED":"true", "API_RADAR_TREND_MODE":"curated",
             "NAVER_BLOG_STORAGE_STATE_JSON":"fake",
             "COUPANG_PARTNERS_ACCESS_KEY":"fake", "COUPANG_PARTNERS_SECRET_KEY":"fake"}
        passed={"exact_product_item_verified":True,"reference_price":40000,
                "discount_pct":25, "history_window":"recent_3_weeks"}
        ordered=[]
        clock={"now":t}
        def submit(article,*args,**kw):
            ordered.append(article["grade"])
            return "https://blog.naver.com/mw5114/" + str(100000000+len(ordered))
        with tempfile.TemporaryDirectory() as td:
            image=Path(td)/"photo.jpg"
            image.write_bytes(b"\xff\xd8\xff"+b"0"*1200)
            with patch.dict(os.environ,env,clear=True), \
                 patch("api_radar.publisher.utc_now",side_effect=lambda:clock["now"]), \
                 patch("api_radar.publisher.load_state",side_effect=lambda:(state,"sha")), \
                 patch("api_radar.publisher.save_state"), \
                 patch("api_radar.publisher.resolve_exact_identity",
                       side_effect=lambda p:{"product_id":p["id"],"item_id":"98765432"}), \
                 patch("api_radar.publisher.check_exact_history",
                       side_effect=lambda p,i: passed if p["id"]=="12345678"
                           else (_ for _ in ()).throw(
                               quality.QualityError("DISCOUNT_BELOW_THRESHOLD_OR_IMPLAUSIBLE"))), \
                 patch("api_radar.publisher.download_verified_image",return_value=image), \
                 patch("gh_only.coupang_partners_api.create_product_deeplink",
                       return_value={"partners_link":"https://link.coupang.com/a/Abcd12345"}), \
                 patch("gh_only.naver_blog_deal_publisher.submit_one",side_effect=submit):
                self.assertEqual(publisher.run(now=clock["now"]), 0)
                self.assertEqual(ordered, ["A"])
                self.assertEqual(publisher.run(now=clock["now"]), 0)
                self.assertEqual(ordered, ["A"])
                clock["now"] += timedelta(minutes=15)
                self.assertEqual(publisher.run(now=clock["now"]), 0)
                self.assertEqual(ordered, ["A", "B"])
                self.assertEqual({v["grade"] for v in state["published"].values()}, {"A","B"})

    def test_goldbox_current_day_queue_dedup_and_no_daily_quota(self):
        t = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)
        def item(pid, category):
            return {"name": "생활용품 정품 특가 " + pid,
                    "source": "goldbox", "price": 12900,
                    "category": category, "image": "https://image15.coupangcdn.com/x.jpg",
                    "url": "https://link.coupang.com/re/AFFSDP?pageKey="
                           + pid + "&itemId=98765432",
                    "checked_at": t.isoformat()}
        state = {"products": {}, "published": {
            str(10000000 + n): {"kst_date": "2026-11-14"}
            for n in range(30)},
            "api_publish_claims": {},
            "goldbox_today": {"kst_date": "2026-11-14",
                              "updated_at": t.isoformat(),
                              "items": {"12345678": item("12345678","생활용품"),
                                        "22345678": item("22345678","생활용품"),
                                        "32345678": item("32345678","국내투어")}}}
        state["published"]["22345678"] = {"kst_date": "2026-11-13"}
        picked = publisher.select_candidates(state, t + timedelta(minutes=5))
        self.assertEqual([x["id"] for x in picked], ["12345678"])
        self.assertEqual(publisher.daily_slot_count(state, t), 30)
        self.assertFalse(publisher.select_candidates(state, t + timedelta(days=1)))
        state["goldbox_today"] = {
            "kst_date":"2026-11-15", "updated_at":(t+timedelta(days=1)).isoformat(),
            "items":{"12345678": item("12345678","생활용품"),
                     "42345678": item("42345678","문구/사무용품")}}
        state["goldbox_today"]["items"]["42345678"]["checked_at"] = (t+timedelta(days=1)).isoformat()
        state["goldbox_today"]["items"]["12345678"]["checked_at"] = (t+timedelta(days=1)).isoformat()
        state["published"]["12345678"]={"kst_date":"2026-11-14"}
        picked_next=publisher.select_candidates(state, t+timedelta(days=1,minutes=5))
        self.assertEqual([x["id"] for x in picked_next],["42345678"])

    def test_claim_before_submit_and_checkpoint(self):
        now = datetime(2026, 11, 13, 5, tzinfo=timezone.utc)
        url = "https://link.coupang.com/re/AFFSDP?pageKey=12345678&itemId=98765432"
        source = {"id": "12345678", "price": 30000, "checked_at": now.isoformat(),
                  "url": url, "source": "goldbox", "category": "생활용품"}
        state = {"products": {"12345678": {
            "last_price": 30000, "last_source": "goldbox",
            "last_product_url": url, "last_checked_at_utc": now.isoformat()
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
