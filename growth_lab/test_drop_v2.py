"""Offline regression tests for verified-only V2. Never issue real HTTP or posts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from growth_lab import price_radar, drop_publisher
from api_radar.publisher import pending_count

T = datetime(2026, 11, 15, 1, 35, tzinfo=timezone.utc)
PID, ITEM, VENDOR = "12345678", "98765432", "45678901"
URL = ("https://link.coupang.com/re/AFFSDP?pageKey=" + PID +
       "&itemId=" + ITEM + "&vendorItemId=" + VENDOR)
ISSUED = "https://link.coupang.com/a/TestOfficial12345"


def api_row(price=29900, vendor=VENDOR):
    return {"productId": int(PID), "productName": "생활용품 정품 고급 필터 세트",
            "productPrice": price, "productUrl": URL.replace(VENDOR, vendor),
            "productImage": "https://image15.coupangcdn.com/test.jpg",
            "isRocket": True, "isFreeShipping": True, "categoryName": "생활용품"}


def prior_prices(now=T):
    dates = ["2026-10-25", "2026-10-28", "2026-10-31",
             "2026-11-04", "2026-11-08", "2026-11-13"]
    hist = [{"day": day, "price": 45000} for day in dates]
    ticks = [{"at": (now - timedelta(hours=24)).isoformat(), "price": 40000}]
    return hist, ticks


def signal(now=T):
    hist, ticks = prior_prices(now)
    return price_radar.classify_v2(hist, ticks, 29900, now)


def price_state(now=T):
    p = price_radar.candidate(api_row(), "1014")
    hist, ticks = prior_prices(now)
    state = {"schema":price_radar.SCHEMA,
             "items":{p["key"]:{"history":hist, "ticks":ticks}},
             "events":[]}
    state, events = price_radar.merge(state, [p], now)
    return state, events[0]


def radar_state():
    return {"schema":"api-radar-snapshot-v1", "products":{},
            "published":{}, "api_publish_claims":{}}


class PriceV2Tests(TestCase):
    def test_category_rotation_visits_all_six_with_two_calls_per_slot(self):
        slots = []
        for h in (9, 11, 13, 15):
            now = T.astimezone(price_radar.KST).replace(hour=h)
            pair = price_radar.categories_for_run(now)
            self.assertEqual(len(pair), 2)
            self.assertEqual(len(set(pair)), 2)
            slots.append(pair)
        self.assertEqual(len({i for pair in slots[:3] for i in pair}), 6)
        self.assertEqual(slots[0], slots[3])

    def test_real_drop_grade_and_fake_discount_guards(self):
        result = signal()
        self.assertEqual(result["grade"], "S")
        self.assertGreater(result["drop_vs_24h_pct"], 20)
        self.assertGreater(result["drop_vs_30d_median_pct"], 25)
        hist, ticks = prior_prices()
        self.assertIsNone(price_radar.classify_v2(hist, [], 29900, T))
        self.assertIsNone(price_radar.classify_v2(hist[:3], ticks, 29900, T))
        self.assertIsNone(price_radar.classify_v2(hist, ticks, 39000, T))
        self.assertIsNone(price_radar.classify_v2(hist, ticks, 10000, T))
        self.assertIsNone(price_radar.classify_v2(
            hist, [{"at":(T-timedelta(hours=2)).isoformat(),"price":45000}], 29900, T))

    def test_merge_dedup_and_vendor_variant_change_discards_history(self):
        state, event = price_state()
        self.assertEqual(event["grade"], "S")
        self.assertEqual(event["source"], "category_best")
        self.assertEqual(event["price"], 29900)
        self.assertEqual(event["vendor_item_id"], VENDOR)
        same = price_radar.candidate(api_row(), "1014")
        state, repeat = price_radar.merge(state, [same], T+timedelta(minutes=3))
        self.assertEqual(repeat, [])
        self.assertEqual(len(state["events"]), 1)
        alt = price_radar.candidate(api_row(vendor="55555555"), "1014")
        state, changed = price_radar.merge(state, [alt], T+timedelta(hours=1))
        self.assertEqual(changed, [])
        self.assertEqual(len(state["items"][same["key"]]["history"]), 1)
        self.assertEqual(state["items"][same["key"]]["vendor_item_id"], "55555555")

    def test_old_events_freshness_duplicate_and_claims(self):
        state, event = price_state()
        self.assertEqual(len(price_radar.eligible_events(state, T)), 1)
        self.assertEqual(price_radar.eligible_events(state, T+timedelta(hours=3)), [])
        self.assertEqual(price_radar.eligible_events(
            state, T, published={PID:{"at":T.isoformat()}}), [])
        self.assertEqual(price_radar.eligible_events(
            state, T, claims={PID:{"claimed_at":T.isoformat()}}), [])
        state["items"][event["key"]]["last_price"] += 100
        self.assertEqual(price_radar.eligible_events(state, T), [])

    def test_preflight_does_not_open_browser_or_lookup_api(self):
        st = radar_state()
        with patch.dict(os.environ, {"API_RADAR_VERIFIED_ONLY":"true"}, clear=True), \
             patch("api_radar.publisher.load_state", return_value=(st, "sha")), \
             patch("growth_lab.drop_publisher.load_growth", return_value=(
                 {"schema":price_radar.SCHEMA,"items":{},"events":[]}, "sha")), \
             patch("growth_lab.price_radar.fetch_category") as get:
            self.assertEqual(pending_count(now=T), 0)
        get.assert_not_called()


class PublishV2Tests(TestCase):
    def test_article_true_median_and_cta(self):
        _, event = price_state()
        offer = price_radar.candidate(api_row(), "1014")
        with tempfile.TemporaryDirectory() as td:
            photo = Path(td) / "p.jpg"
            photo.write_bytes(b"JPEG" + b"0"*1200)
            article = drop_publisher.compose_drop_article(event, offer, photo, ISSUED)
            self.assertTrue(article["title"].startswith("[쿠팡🔥핫딜]"))
            self.assertIn("관측가 중앙값", article["body"])
            self.assertIn("하루 전 대비", article["body"])
            self.assertIn("공식 정가 대비 할인율이 아닙니다", article["body"])
            self.assertEqual(article["grade"], "A")
            self.assertEqual(article["v2_grade"], "S")
            self.assertNotIn(ISSUED, article["body"])

    def test_live_revalidation_rejects_wrong_option_price_vendor(self):
        _, event = price_state()
        self.assertIsNotNone(drop_publisher.validated_offer(event, [api_row()]))
        self.assertIsNone(drop_publisher.validated_offer(event, [api_row(price=30000)]))
        self.assertIsNone(drop_publisher.validated_offer(
            event, [api_row(vendor="55555555")]))
        self.assertIsNone(drop_publisher.validated_offer(event, []))

    def test_safe_claim_before_submit_and_checkpoint(self):
        growth, event = price_state()
        global_state = radar_state()
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "p.jpg"
            image.write_bytes(b"JPEG" + b"0"*1200)
            saved = []
            def save(st, sha):
                saved.append((len(st["api_publish_claims"]),len(st["published"])))
            with patch.dict(os.environ, {"NAVER_BLOG_STORAGE_STATE_JSON":"fake"},clear=True), \
                 patch("growth_lab.drop_publisher.load_growth", return_value=(growth,"sha")), \
                 patch("growth_lab.drop_publisher.load_state", return_value=(global_state,"sha")), \
                 patch("api_radar.publisher.utc_now", return_value=T), \
                 patch("growth_lab.drop_publisher.price_radar.fetch_category",
                       return_value=[api_row()]) as api, \
                 patch("growth_lab.drop_publisher.download_verified_image",
                       return_value=image), \
                 patch("gh_only.coupang_partners_api.create_product_deeplink",
                       return_value={"partners_link":ISSUED}), \
                 patch("gh_only.naver_blog_deal_publisher.submit_one",
                       return_value="https://blog.naver.com/mw5114/123456789") as submit, \
                 patch("growth_lab.drop_publisher.save_state", side_effect=save):
                self.assertEqual(drop_publisher.run_one(now=T), 0)
            self.assertEqual(api.call_count, 1)
            submit.assert_called_once()
            self.assertEqual(saved, [(1,0),(0,1)])
            self.assertEqual(global_state["published"][PID]["source"],
                             "growth_price_drop_v2")
            self.assertEqual(global_state["published"][PID]["v2_grade"], "S")

    def test_ambiguous_submit_never_retries(self):
        growth, _ = price_state()
        global_state = radar_state()
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "p.jpg"
            image.write_bytes(b"JPEG" + b"0"*1200)
            with patch.dict(os.environ, {"NAVER_BLOG_STORAGE_STATE_JSON":"fake"},clear=True), \
                 patch("growth_lab.drop_publisher.load_growth", return_value=(growth,"sha")), \
                 patch("growth_lab.drop_publisher.load_state", return_value=(global_state,"sha")), \
                 patch("api_radar.publisher.utc_now", return_value=T), \
                 patch("growth_lab.drop_publisher.price_radar.fetch_category",
                       return_value=[api_row()]), \
                 patch("growth_lab.drop_publisher.download_verified_image",
                       return_value=image), \
                 patch("gh_only.coupang_partners_api.create_product_deeplink",
                       return_value={"partners_link":ISSUED}), \
                 patch("gh_only.naver_blog_deal_publisher.submit_one",
                       side_effect=RuntimeError("uncertain")) as post, \
                 patch("growth_lab.drop_publisher.save_state"):
                self.assertEqual(drop_publisher.run_one(now=T), 1)
            self.assertEqual(post.call_count, 1)
            self.assertIn(PID, global_state["api_publish_claims"])
            self.assertNotIn(PID, global_state["published"])


if __name__ == "__main__":
    main()
