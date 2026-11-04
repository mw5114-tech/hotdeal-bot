"""Offline-only tests of first Naver product-price comparison fallback."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gh_only.naver_web_product_price import (
    parse_first_card, fetch_first_naver_product_price,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.lowchart_price_reference import build_url, parse_reference

PID, IID = "9217210892", "27232840915"
TITLE = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개"
LOW_TITLE = TITLE + " - 31,340원 - 로우차트"
ID = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": PID,
      "item_id": IID, "vendor_item_id": "94199974724"}
DEAL = {
    "source_message_id": 987654,
    "source_post_url": "https://t.me/jirum/987654",
    "original_purchase_url": "https://link.coupang.com/a/example",
    "source_text": "☆특가 12,050원\n" + TITLE,
    "title": TITLE,
    "sale_price_krw": 12050,
}
OG = {"status": "LOWCHART_EXACT_ITEM_IMAGE", "og_url": build_url(PID, IID),
      "og_title": LOW_TITLE, "lowchart_title_verified": True,
      "og_image": "https://thumbnail.coupangcdn.com/image.jpg",
      "image_source": "lowchart_exact_item_public_page"}
CARD = {"price_text": "19,900", "card_text": "다른 상품 옵션\n19,900\n원\n배송비 무료",
        "shopping_link": "https://cr3.shopping.naver.com/v2/bridge/searchGate?nv_mid=12345"}


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakeMouse:
    def wheel(self, *args):
        pass


class FakePage:
    mouse = FakeMouse()
    def __init__(self, row=None, status=200, redirect=None):
        self.row = row
        self.status = status
        self.redirect = redirect
        self.url = ""
        self.visited = []
    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = self.redirect or url
        return FakeResponse(self.status)
    def wait_for_timeout(self, ms):
        pass
    def evaluate(self, script):
        self.script = script
        return self.row
    def close(self):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.page = page
    def new_page(self, **kwargs):
        return self.page


class NaverPriceTests(unittest.TestCase):
    def test_first_shopping_result_used_even_when_option_differs(self):
        page = FakePage(CARD)
        r = fetch_first_naver_product_price(TITLE, chromium=FakeBrowser(page))
        self.assertEqual(r["status"], "PRICE_FOUND")
        self.assertEqual(r["price_krw"], 19900)
        self.assertFalse(r["product_option_verified"])
        self.assertFalse(r["price_is_historical_average"])
        self.assertIn("query=", page.visited[0])
        self.assertIn("cr3.shopping.naver.com", page.script)

    def test_plain_snippet_and_foreign_links_cannot_supply_price(self):
        self.assertIsNone(parse_first_card(
            {"price_text":"3,890,000", "card_text":"블로그 상품 소개",
             "shopping_link":"https://blog.naver.com/example"}))
        self.assertIsNone(parse_first_card(
            {"price_text":"80,000", "card_text":"쇼핑",
             "shopping_link":"https://cr3.shopping.naver.com.evil.org/v2/bridge/searchGate"}))
        self.assertIsNone(parse_first_card(
            {"price_text":"0", "card_text":"쇼핑", "shopping_link":CARD["shopping_link"]}))
        self.assertEqual(parse_first_card(CARD), 19900)

    def test_blocked_and_unavailable_results_do_not_create_price(self):
        blocked = fetch_first_naver_product_price(
            TITLE, chromium=FakeBrowser(FakePage(CARD, status=418)))
        self.assertEqual(blocked["status"], "NAVER_PRICE_SEARCH_BLOCKED")
        missing = fetch_first_naver_product_price(
            TITLE, chromium=FakeBrowser(FakePage()))
        self.assertEqual(missing["status"], "NAVER_PRICE_CARD_NOT_FOUND")
        self.assertIsNone(missing["price_krw"])

    def test_lowchart_missing_mean_uses_naver_first_card_to_compute_discount(self):
        called = []
        def naver(title):
            called.append(title)
            return {**CARD, "status": "PRICE_FOUND", "price_krw": 19900}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "true"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value=OG):
                    result = enrich_deal(
                        DEAL, Path(tmp), resolver=lambda _: ID,
                        history_fetch=lambda *args: {},
                        photo_downloader=lambda *args: {"status":"IMAGE_SAVED", "saved_name":"test.jpg"},
                        owner_tag="AF9668915", naver_price_lookup=naver)
        self.assertEqual(called, [TITLE])
        self.assertFalse(result["history_avg_accepted"])
        self.assertEqual(result["comparison_price_source"],
                         "naver_general_search_first_shopping_card")
        self.assertIsNone(result["caption"]["observed_average_krw"])
        self.assertEqual(result["caption"]["comparison_reference_price_krw"], 19900)
        self.assertEqual(result["caption"]["approx_discount_vs_reference_pct"], 40)
        self.assertIn("💸 비교 기준가(네이버 검색) : 약 19,900원",
                      result["caption"]["caption"])
        self.assertIn("[40%할인]", result["caption"]["caption"])
        self.assertEqual(result["caption"]["sale_price_krw"], 12050)
        self.assertFalse(result["observed_low_verified_for_deal"])

    def test_verified_lowchart_mean_skips_naver_completely(self):
        html = (f"<html><title>{LOW_TITLE}</title><body>productId={PID} itemId={IID} "
                "현재가 31,340원 관측 최저가 20,820원 관측 최고가 48,590원 "
                "관측 평균가 34,575원 최근 변동일 2026-10-31</body></html>")
        def history(p, i, tokens, sale):
            return parse_reference(html, build_url(p,i),p,i,tokens,sale)
        def never(title):
            raise AssertionError("Naver must not be called when Lowchart has a mean")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "true"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value=OG):
                    result = enrich_deal(
                        DEAL, Path(tmp), resolver=lambda _:ID,
                        history_fetch=history, naver_price_lookup=never,
                        photo_downloader=lambda *args: {"status":"IMAGE_SAVED"},
                        owner_tag="AF9668915")
        self.assertTrue(result["history_avg_accepted"])
        self.assertEqual(result["caption"]["observed_average_krw"], 34575)
        self.assertIn("쿠팡 평소 판매가 : 약 34,575원", result["caption"]["caption"])
        self.assertEqual(result["naver_price_lookup"]["status"], "NOT_ATTEMPTED")

    def test_naver_block_leaves_discount_unconfirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "true"}):
                with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                           return_value=OG):
                    result = enrich_deal(
                        DEAL, Path(tmp), resolver=lambda _: ID,
                        history_fetch=lambda *args: {}, naver_price_lookup=lambda title: {
                            "status":"NAVER_PRICE_SEARCH_BLOCKED", "price_krw": None,
                        }, photo_downloader=lambda *args: {"status":"IMAGE_SAVED"},
                        owner_tag="AF9668915")
        self.assertEqual(result["caption"]["comparison_reference_price_krw"], None)
        self.assertIn("할인율 확인 중", result["caption"]["caption"])
        self.assertIsNone(result["comparison_price_source"])


if __name__ == "__main__":
    unittest.main()
