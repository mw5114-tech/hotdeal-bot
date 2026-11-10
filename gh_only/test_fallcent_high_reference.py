"""Offline regression for source-verified Fallcent historical high price.

No calls to Fallcent, Naver, Coupang or Daangn.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gh_only.fallcent_high_reference import (
    fetch_exact_fallcent_high, parse_visible_high,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.default_deal_format import create_default_preview
from gh_only.jirum_manual_queue import validate_draft

TITLE = "태평염전 간수뺀 햇소금 신안 천일염 탈수, 10kg, 1개"
IDENTITY = {
    "status": "EXACT_ITEM_IDENTIFIED",
    "product_id": "8863489459", "item_id": "28741784521",
    "vendor_item_id": "999999777"
}
DEAL = {
    "source_message_id": 96123,
    "source_post_url": "https://t.me/jirum/96123",
    "original_purchase_url": "https://link.coupang.com/a/ioWODkPbz2",
    "source_text": "☆특가 11,900원\n" + TITLE + "\n📦 특가 바로가기\nhttps://link.coupang.com/a/ioWODkPbz2",
    "title": TITLE, "sale_price_krw": 11900,
}
BASE = (
    '<html><head><title>폴센트 상품 페이지</title>'
    '<script>document.write("역대급최고가 999,999원")</script>'
    '</head><body><main><h1>' + TITLE + '</h1>'
    '<section><h2>가격 그래프</h2>'
    '<div><span>▲ 역대급최고가</span><strong>27,000원</strong></div>'
    '<div>현재가 12,000원</div><div>역대급최저가 10,500원</div>'
    '</section></main><footer>역대급최고가, 역대급최저가는 그래프 기준입니다.</footer>'
    '</body></html>'
)
IMAGE_BYTES = b"\xff\xd8\xff" + b"\0" * 1200


class FakeResponse:
    status = 200
    def __init__(self, content, final):
        self.content = content.encode("utf-8")
        self.final = final
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return self.final
    def read(self, amount): return self.content[:amount]


def photo(title, output_dir, source_id):
    filename = f"jirum_{source_id}_fallcent_photo.jpg"
    (output_dir / filename).write_bytes(IMAGE_BYTES)
    return {
        "status": "IMAGE_SAVED",
        "saved_name": filename,
        "bytes": len(IMAGE_BYTES),
        "image_source": "fallcent_search_representative",
        "image_match_basis": "UNVERIFIED_FALLCENT_SEARCH_RESULT",
        "representative_fallcent_image": True,
        "image_provider_title": TITLE,
    }


def high_record(amount=27000):
    return {
        "status": "HIGH_FOUND",
        "observed_high_krw": amount,
        "price_basis": "fallcent_public_visible_historical_high",
        "source_title": TITLE,
        "source_url": "https://www.fallcent.com/product/example-hash/",
        "product_id": IDENTITY["product_id"],
        "item_id": IDENTITY["item_id"],
    }


def process(folder: Path, high, *, naver=None, history=None):
    env = {"JIRUM_IMAGE_SOURCE": "lowchart",
           "JIRUM_ENABLE_NAVER_PRICE_FALLBACK": "true",
           "COUPANG_PARTNER_LPTAG": "AF9668915"}
    with patch.dict(os.environ, env):
        with patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value={"status": "LOWCHART_ITEM_PAGE_NOT_FOUND"}):
            return enrich_deal(
                DEAL, folder, resolver=lambda link: IDENTITY,
                history_fetch=history or (lambda *args: {}),
                owner_tag="AF9668915",
                fallcent_lookup=photo,
                fallcent_high_lookup=lambda identity, title: high,
                naver_price_lookup=naver or (lambda title: {
                    "status": "NAVER_PRICE_CARD_NOT_FOUND", "price_krw": None}))


class FallcentHighTests(unittest.TestCase):
    def test_visible_high_ignored_fake_script_and_footer(self):
        result = parse_visible_high(BASE, TITLE)
        self.assertEqual(result["status"], "HIGH_FOUND")
        self.assertEqual(result["observed_high_krw"], 27000)
        self.assertNotEqual(result["observed_high_krw"], 999999)

    def test_missing_high_never_inferred_from_current_or_low(self):
        doc = BASE.replace("<div><span>▲ 역대급최고가</span><strong>27,000원</strong></div>", "")
        result = parse_visible_high(doc, TITLE)
        self.assertEqual(result["status"], "FALLCENT_HIGH_NOT_VISIBLE")
        self.assertIsNone(result["observed_high_krw"])

    def test_wrong_product_title_is_blocked(self):
        out = parse_visible_high(BASE, "삼성전자 85인치 TV")
        self.assertEqual(out["status"], "FALLCENT_PRODUCT_TITLE_MISMATCH")

    def test_multiple_conflicting_highs_are_rejected(self):
        out = parse_visible_high(BASE.replace(
            "</section>", "<p>역대급최고가 31,000원</p></section>"), TITLE)
        self.assertEqual(out["status"], "FALLCENT_HIGH_AMBIGUOUS")

    def test_exact_item_url_and_server_redirect_are_checked(self):
        observed = []
        def opener(req, timeout):
            observed.append(req.full_url)
            return FakeResponse(BASE, "https://www.fallcent.com/product/testHash/")
        out = fetch_exact_fallcent_high(IDENTITY, TITLE, opener=opener)
        self.assertEqual(out["status"], "HIGH_FOUND")
        self.assertEqual(out["observed_high_krw"], 27000)
        self.assertIn("item_id=28741784521", observed[0])
        self.assertIn("product_id=8863489459", observed[0])
        self.assertEqual(out["item_id"], IDENTITY["item_id"])
        def bad_redirect(req, timeout):
            return FakeResponse(BASE, "https://not-fallcent.example/product/testHash/")
        out = fetch_exact_fallcent_high(IDENTITY, TITLE, opener=bad_redirect)
        self.assertEqual(out["status"], "FALLCENT_RESPONSE_INVALID")

    def test_invalid_id_never_queries_network(self):
        invalid = {**IDENTITY, "item_id": "../../etc/passwd"}
        out = fetch_exact_fallcent_high(invalid, TITLE,
                                       opener=lambda *args: self.fail("network"))
        self.assertEqual(out["status"], "INVALID_PRODUCT_IDENTITY")

    def test_high_wins_over_naver_when_photo_from_fallcent(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            row = process(folder, high_record(),
                          naver=lambda title: self.fail("Naver called for verified high"))
            self.assertTrue(row["representative_fallcent_image"])
            self.assertEqual(row["comparison_price_source"], "fallcent_observed_high")
            self.assertEqual(row["caption"]["comparison_reference_price_krw"], 27000)
            self.assertEqual(row["caption"]["approx_discount_vs_reference_pct"], 55)
            self.assertIsNone(row["caption"]["observed_average_krw"])
            self.assertIn("💸 폴센트 관측 최고가 : 약 27,000원", row["caption"]["caption"])
            self.assertNotIn("쿠팡 평소 판매가 : 약 27,000원", row["caption"]["caption"])
            self.assertIn("[55%할인]", row["caption"]["caption"])
            (folder / "drafts.json").write_text(json.dumps({"schema":"github-jirum-default-price-photo-affsdp-draft-v2","drafts":[row]},ensure_ascii=False),"utf-8")
            with patch.dict(os.environ, {"JIRUM_IMAGE_SOURCE":"lowchart",
                                         "COUPANG_PARTNER_LPTAG":"AF9668915"}):
                article = validate_draft(folder, 96123)
            self.assertIn("폴센트 관측 최고가", article["body"])

    def test_tampered_high_proof_never_reaches_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            row = process(folder, high_record(),
                          naver=lambda title: self.fail("No Naver"))
            row["fallcent_high_lookup"]["item_id"] = "1122334455"
            (folder / "drafts.json").write_text(json.dumps({"schema":"github-jirum-default-price-photo-affsdp-draft-v2","drafts":[row]},ensure_ascii=False),"utf-8")
            with patch.dict(os.environ, {"JIRUM_IMAGE_SOURCE":"lowchart",
                                         "COUPANG_PARTNER_LPTAG":"AF9668915"}):
                with self.assertRaisesRegex(RuntimeError,"FALLCENT_HIGH_EVIDENCE_MISMATCH"):
                    validate_draft(folder, 96123)

    def test_no_public_high_never_uses_naver_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = process(Path(tmp), {"status":"FALLCENT_HIGH_NOT_VISIBLE",
                                      "observed_high_krw":None},
                          naver=lambda title: self.fail("Naver must stay disabled"))
        self.assertIsNone(row["comparison_price_source"])
        self.assertEqual(row["naver_price_lookup"]["status"],
                         "DISABLED_BY_USER_PRICE_SOURCE_POLICY")
        self.assertIn("확인 중", row["caption"]["caption"])
        self.assertNotIn("네이버", row["caption"]["caption"])

    def test_public_exact_item_high_low_yields_labelled_midpoint(self):
        high = {**high_record(),
                "observed_low_krw":10500,
                "low_basis":"fallcent_public_visible_historical_low"}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            row = process(folder, high, naver=lambda title: self.fail("Naver"))
            self.assertEqual(row["comparison_price_source"],
                             "fallcent_high_low_midpoint")
            self.assertEqual(row["caption"]["comparison_reference_price_krw"],18750)
            self.assertIn("폴센트 최고·최저 중간값 : 약 18,750원",row["caption"]["caption"])
            self.assertIsNone(row["caption"]["observed_average_krw"])
            (folder / "drafts.json").write_text(json.dumps({
                "schema":"github-jirum-default-price-photo-affsdp-draft-v2",
                "drafts":[row]},ensure_ascii=False),"utf-8")
            with patch.dict(os.environ,{"JIRUM_IMAGE_SOURCE":"lowchart",
                                        "COUPANG_PARTNER_LPTAG":"AF9668915"}):
                validate_draft(folder,96123)

    def test_public_current_without_high_is_truthfully_labeled(self):
        current = {
            "status":"FALLCENT_HIGH_NOT_VISIBLE",
            "observed_high_krw":None,"observed_current_krw":12000,
            "current_basis":"fallcent_public_visible_current",
            "source_title":TITLE,
            "source_url":"https://www.fallcent.com/product/testHash/",
            "product_id":IDENTITY["product_id"],"item_id":IDENTITY["item_id"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            row = process(Path(tmp),current,
                          naver=lambda title:self.fail("Naver"))
        self.assertEqual(row["comparison_price_source"],"fallcent_observed_current")
        self.assertIn("폴센트 관측 현재가 : 약 12,000원",row["caption"]["caption"])
        self.assertNotIn("[5%할인]",row["caption"]["caption"])

    def test_visible_exact_item_current_low_without_high(self):
        doc = BASE.replace(
            '<div><span>▲ 역대급최고가</span><strong>27,000원</strong></div>',""
        )
        parsed = parse_visible_high(doc,TITLE)
        self.assertEqual(parsed["status"],"FALLCENT_HIGH_NOT_VISIBLE")
        self.assertEqual(parsed["observed_current_krw"],12000)
        self.assertEqual(parsed["observed_low_krw"],10500)
        self.assertIsNone(parsed["observed_high_krw"])

    def test_visible_conflicting_current_is_rejected(self):
        doc = BASE.replace("</section>","<div>현재가 99,999원</div></section>")
        parsed = parse_visible_high(doc,TITLE)
        self.assertEqual(parsed["status"],"FALLCENT_PRICES_AMBIGUOUS")
        self.assertIsNone(parsed["observed_current_krw"])

    def test_untrusted_high_without_exact_proof_is_ignored(self):
        bad = {**high_record(), "product_id": "2233445566"}
        with tempfile.TemporaryDirectory() as tmp:
            row = process(Path(tmp), bad)
        self.assertNotEqual(row.get("comparison_price_source"),"fallcent_observed_high")

    def test_format_refuses_to_label_maximum_as_historical_average(self):
        output = create_default_preview(11900,TITLE,27000,
                                        reference_source="fallcent_observed_high")
        self.assertIsNone(output["observed_average_krw"])
        self.assertFalse(output["comparison_price_is_historical_average"])
        self.assertIsNone(output["actual_discount_vs_historical_mean_pct"])
        self.assertEqual(output["actual_discount_vs_reference_pct"],55.9)


if __name__ == "__main__":
    unittest.main()
