"""Pure offline tests of the default pipeline; zero HTTP, login, or posting."""
import tempfile
from pathlib import Path
import unittest

from gh_only.default_deal_automation import (
    enrich_deal, og_matches_product, get_title_tokens
)
from gh_only.lowchart_price_reference import parse_reference, build_url

PID, IID, VID = "9217210892", "27232840915", "94199974724"
SOURCE = "https://link.coupang.com/a/iiPBYpVHae"
OG_TITLE = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개 - 즉석카레 | 쿠팡"
H_TITLE = "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개 - 31,340원 - 로우차트"
OG_URL = ("https://www.coupang.com/vp/products/9217210892"
          "?vendorItemId=94199974724")
HIST_HTML = f"""<html><head><title>{H_TITLE}</title></head><body>
product 9217210892 item 27232840915
현재가 31,340원 관측 최저가 20,820원 관측 최고가 48,590원
관측 평균가 34,575원 최근 변동일 2026-10-31</body></html>"""
DEAL = {
    "source_message_id": 9000101,
    "source_post_url": "https://t.me/jirum/9000101",
    "source_text": (
        "🔥 무지성 12,050원 (59.8% 할인)\n"
        "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개\n"
        "- 정상가: 30,000원\n- 할인: 17,950원\n"
        "https://link.coupang.com/a/iiPBYpVHae"
    ),
    "title": "휘카레 셰프 오리지널 소스 순한맛, 230g, 20개",
    "original_purchase_url": SOURCE,
    "sale_price_krw": 12050,
}
IDENTITY = {
    "status": "EXACT_ITEM_IDENTIFIED",
    "product_id": PID, "item_id": IID, "vendor_item_id": VID,
}
OG = {
    "status": "KAKAO_METADATA_READ",
    "og_title": OG_TITLE,
    "og_url": OG_URL,
    "og_image": "https://thumbnail.coupangcdn.com/example.jpg"
}


class AutomatedDefaultTests(unittest.TestCase):
    def test_og_identity_vendor_item_matching(self):
        self.assertTrue(og_matches_product(OG, IDENTITY))
        self.assertFalse(og_matches_product({**OG, "og_url": OG_URL.replace(VID,"00000")}, IDENTITY))
        self.assertFalse(og_matches_product({**OG, "og_url": OG_URL.replace(PID,"00000")}, IDENTITY))
        self.assertFalse(og_matches_product({**OG, "og_url": "https://evil.example/vp/products/" + PID}, IDENTITY))

    def test_kakao_og_title_to_strict_tokens(self):
        self.assertEqual(get_title_tokens(OG_TITLE), ("휘카레", "230g", "20개"))

    def test_combines_default_caption_average_and_photo(self):
        calls = []
        def resolver(link):
            calls.append(("resolve", link))
            return IDENTITY
        def og_fn(link, state):
            calls.append(("kakao", link))
            return OG
        def history_fn(p, i, tokens, price):
            calls.append(("lowchart", p, i, tokens, price))
            return parse_reference(HIST_HTML,build_url(p,i),p,i,tokens,price)
        def photo_fn(url, folder, mid):
            calls.append(("image", url, mid))
            return {"status":"IMAGE_SAVED", "saved_name":"jirum_9000101_kakao_og.jpg"}
        with tempfile.TemporaryDirectory() as tmp:
            r = enrich_deal(DEAL, Path(tmp), resolver, og_fn, history_fn, photo_fn, "test")
        self.assertEqual([x[0] for x in calls], ["resolve","kakao","lowchart","image"])
        self.assertTrue(r["history_avg_accepted"])
        self.assertEqual(r["caption"]["approx_discount_vs_historical_mean_pct"], 65)
        self.assertEqual(r["caption"]["observed_average_krw"], 34575)
        self.assertEqual(r["caption"]["caption"].splitlines(), [
            "📦 상품명 : 휘카레 셰프 오리지널 소스 순한맛, 230g, 20개",
            "💸 쿠팡 평소 판매가(로우차트 관측 평균) : 약 34,575원",
            "🔥 핫딜 특가 : 12,050원 (상세페이지 참고 / 약 65% 혜택)",
        ])
        self.assertNotIn("59.8%", r["caption"]["caption"])
        self.assertFalse(r["caption"]["auto_publish_ready"])
        self.assertFalse(r["personal_affiliate_link_created"])

    def test_kakao_vendor_conflict_blocks_history_photo(self):
        calls=[]
        def never(*args):
            calls.append(True)
            raise AssertionError("should not call")
        bad = {**OG, "og_url": OG_URL.replace(VID,"987654321")}
        result = enrich_deal(DEAL, Path("."), lambda link: IDENTITY,
                             lambda link, state: bad, never, never, "fake")
        self.assertFalse(result["kakao_product_match"])
        self.assertEqual(result["caption"]["caption"].splitlines()[0],
                         "📦 상품명 : 휘카레 셰프 오리지널 소스 순한맛, 230g, 20개")
        self.assertNotIn("% 혜택", result["caption"]["caption"])
        self.assertEqual(calls, [])

    def test_inexact_link_does_not_submit_url_to_kakao(self):
        def never(*args):
            raise AssertionError("should not connect")
        out = enrich_deal(DEAL, Path("."),
                          lambda link: {"status": "EXACT_ITEM_ID_MISSING"},
                          never, never, never, "")
        self.assertEqual(out["kakao_status"], "UNRESOLVED_COUPANG_ITEM")
        self.assertNotIn("% 혜택", out["caption"]["caption"])

    def test_history_wrong_option_is_safely_ignored(self):
        def wrong(*args):
            bad = HIST_HTML.replace("230g, 20개", "230g, 2개")
            return parse_reference(bad, build_url(PID,IID), PID,IID,
                                   ("휘카레", "230g", "20개"), 12050)
        out = enrich_deal(DEAL, Path("."), lambda link: IDENTITY,
                          lambda link, state: OG, wrong,
                          lambda *args: {"status":"NO_DOWNLOAD"}, "fake")
        self.assertIsNone(out["caption"]["approx_discount_vs_historical_mean_pct"])
        self.assertIn("관측 평균) : 확인 중", out["caption"]["caption"])
        self.assertNotIn("% 혜택", out["caption"]["caption"])


if __name__=="__main__":
    unittest.main()
