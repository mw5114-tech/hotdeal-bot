"""Offline-only Growth Lab tests: never call Coupang, GitHub or Naver."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest import TestCase, main
from unittest.mock import patch

from growth_lab import github_store as store
from growth_lab import revenue, price_radar, seo_drafts

T = datetime(2026, 11, 14, 3, 40, tzinfo=timezone.utc)


class StoreTests(TestCase):
    def test_state_paths_isolated(self):
        for path in (revenue.PATH, price_radar.PATH, seo_drafts.PATH):
            self.assertIn("state/growth_lab_", path)
            self.assertIn("/contents/state/growth_lab_", store._url(path))
        with self.assertRaises(store.GrowthStoreError):
            store._url("state/naver_blog_jirum_fifo.json")
        with self.assertRaises(store.GrowthStoreError):
            store._url("state/growth_lab_../evil")

    def test_store_requires_schema(self):
        with self.assertRaises(store.GrowthStoreError):
            store.save(price_radar.PATH, {"schema":"wrong"}, None,
                       schema=price_radar.SCHEMA)


class RevenueTests(TestCase):
    def test_api_path_is_reporting_not_seller_api(self):
        path = revenue.report_path("commission", "20261113")
        self.assertEqual(path, "/v2/providers/affiliate_open_api/apis/openapi/v1/"
                               "reports/commission?startDate=20261113&endDate=20261113&page=0")
        with self.assertRaises(revenue.RevenueError):
            revenue.report_path("seller", "20261113")
        with self.assertRaises(revenue.RevenueError):
            revenue.report_path("clicks", "20261113", 5)

    def test_actual_api_fields_group_only_and_no_invented_attribution(self):
        rows = {
            "clicks":[{"subId":"naverblog", "click": 14}],
            "orders":[{"subId":"naverblog", "orderId":1234, "quantity":2,
                       "categoryName":"생활용품", "commission":6000}],
            "cancels":[],
            "commission":[{"subId":"naverblog", "commission":5200}]
        }
        s = revenue.summarize(rows, "20261113")
        self.assertEqual(s["reports"]["clicks"]["segments"]["naverblog"], 14)
        self.assertEqual(s["reports"]["orders"]["segments"]["naverblog"], 2)
        self.assertEqual(s["reports"]["commission"]["segments"]["naverblog"], 5200)
        self.assertEqual(s["orders_commission_by_category"]["생활용품"],6000)
        self.assertFalse(s["per_blog_post_attribution_verified"])

    def test_encrypt_decrypt_and_tamper_detection(self):
        env = {"COUPANG_PARTNERS_SECRET_KEY":"some-test-secret-do-not-use-in-production-12345"}
        obj = {"schema":revenue.SCHEMA, "daily":{"20261113":{
            "reports":{"commission":{"segments":{"naverblog":5500}}}}}}
        with patch.dict(os.environ, env, clear=True):
            sealed = revenue.encrypt_report(obj)
            self.assertEqual(sealed["schema"],revenue.CIPHER_SCHEMA)
            self.assertNotIn("naverblog",json.dumps(sealed))
            self.assertEqual(revenue.decrypt_report(sealed),obj)


class CategoryTests(TestCase):
    def test_categories_are_two_distinct_known_ids(self):
        ids = price_radar.categories_for_run(T)
        self.assertEqual(len(ids),2)
        self.assertEqual(len(set(ids)),2)
        self.assertTrue(all(i in price_radar.CATEGORIES for i in ids))

    def test_category_allowlist(self):
        self.assertIn("/products/bestcategories/1016?limit=30",
                      price_radar.category_path("1016"))
        with self.assertRaises(price_radar.PriceRadarError):
            price_radar.category_path("9999")

    def test_exact_item_required(self):
        def row(url):
            return {"productId":12345678, "productName":"저소음 무선 키보드 정품",
                    "productPrice":39900,
                    "productImage":"https://image15.coupangcdn.com/test.jpg",
                    "productUrl":url, "isRocket":True,
                    "isFreeShipping":True, "categoryName":"가전디지털"}
        good = "https://link.coupang.com/re/AFFSDP?pageKey=12345678&itemId=98765432"
        self.assertEqual(price_radar.candidate(row(good),"1016")["key"],
                         "12345678:98765432")
        self.assertIsNone(price_radar.candidate(row(
            "https://link.coupang.com/a/Abcxyz123456"),"1016"))

    def test_price_drop_only_on_multiple_prior_option_days(self):
        history = [{"day":date, "price":50000} for date in
                   ("2026-11-01","2026-11-04","2026-11-07")]
        verified = price_radar.calculate_drop(history,35000,"2026-11-14")
        self.assertAlmostEqual(verified["drop_vs_prior_pct"],30)
        self.assertEqual(verified["observation_days"],3)
        self.assertIsNone(price_radar.calculate_drop(history[:2],35000,"2026-11-14"))
        self.assertIsNone(price_radar.calculate_drop(history,47000,"2026-11-14"))

    def test_alert_once_and_dedupe_on_same_day(self):
        state = {"schema":price_radar.SCHEMA,
                 "items":{"12345678:98765432":{
                     "history":[{"day":"2026-11-01","price":50000},
                                {"day":"2026-11-04","price":50000},
                                {"day":"2026-11-07","price":50000}]}},
                 "events":[]}
        offer = {"key":"12345678:98765432","id":"12345678",
                 "item_id":"98765432","name":"무선 키보드", "category":"가전디지털",
                 "category_id":"1016","price":35000,
                 "url":"https://www.coupang.com/vp/products/12345678?itemId=98765432",
                 "image":"https://image15.coupangcdn.com/test.jpg"}
        state, events = price_radar.merge(state,[offer],T)
        self.assertEqual(len(events),1)
        state, events = price_radar.merge(state,[offer],T+timedelta(hours=1))
        self.assertEqual(events,[])
        self.assertEqual(len(state["events"]),1)


class DraftTests(TestCase):
    def sample(self, number: int):
        pid=str(12345678+number)
        return {"id":pid, "name":"저소음 무선 키보드 모델"+str(number),
                "price":30000+number*1000,"is_rocket":True,
                "free_shipping":False, "url":"https://www.coupang.com/vp/products/"
                     +pid+"?itemId=98765432",
                "image":"https://image15.coupangcdn.com/x.jpg",
                "identity":{"product_id":pid, "item_id":"98765432"},
                "affiliate_url":"https://link.coupang.com/a/TestOfficial123"}

    def test_draft_is_truthful_and_never_published(self):
        doc=seo_drafts.build_draft(seo_drafts.TOPICS[0],
                                   [self.sample(i) for i in range(3)],T)
        self.assertEqual(doc["status"],"DRAFT_REVIEW_REQUIRED")
        self.assertFalse(doc["auto_published"])
        self.assertEqual(doc["article_markdown"].count(
            "https://link.coupang.com/a/TestOfficial123"),3)
        self.assertIn("수집 당시",doc["article_markdown"])
        self.assertNotIn("제가 사용",doc["article_markdown"])
        self.assertNotIn("무조건 추천",doc["article_markdown"])
        self.assertFalse(doc["claim_verified_seo_traffic"])
        with self.assertRaises(seo_drafts.DraftError):
            seo_drafts.build_draft(seo_drafts.TOPICS[0],
                                   [self.sample(i) for i in range(2)],T)

    def test_draft_dedup_checks_before_api_call(self):
        topic=seo_drafts.topic_for_day(T)
        key=T.astimezone(seo_drafts.KST).date().isoformat()+":"+topic[0]
        state={"schema":seo_drafts.SCHEMA,
               "drafts":[{"key":key,"status":"DRAFT_REVIEW_REQUIRED"}]}
        with patch("growth_lab.seo_drafts.load",return_value=(state,"sha")), \
             patch("growth_lab.seo_drafts.get_api") as api:
            self.assertEqual(seo_drafts.run(now=T),0)
            api.assert_not_called()


if __name__ == "__main__":
    main()
