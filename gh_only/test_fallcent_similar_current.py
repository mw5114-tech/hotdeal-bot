"""No-network tests for Fallcent similar-product displayed CURRENT price.

Different quantity/options deliberately pass product-family matching, but
the resulting copy must NOT claim an exact-item normal price or percent off.
"""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gh_only.fallcent_similar_current import (
    STATUS, select_similar_current, fetch_similar_current, valid_search_price,
)
from gh_only.default_deal_automation import enrich_deal
from gh_only.jirum_manual_queue import validate_draft
from gh_only.naver_blog_format import build_blocks, validate_price_presentation
from gh_only.test_fallcent_search_photo import (
    TITLE, SALT_ID, DEAL, IMG_URL, PNG, FakeResponse,
)
from gh_only.default_deal_format import create_default_preview


SEARCH_URL = "https://fallcent.com/product/search/?keyword=26%EB%85%84+..."
RESULT_URL = "https://fcnt.link/salt20"
# Same salt family, DIFFERENT package size: deliberately permitted.
CARD_TITLE = "태평염전 간수뺀 신안 천일염 탈수 20kg 1개 17,420원"
HTML = f'''<html><head><meta property="og:price" content="99999"></head><body>
<a href="https://fcnt.link/unrelated">애플 아이폰 정품 250,000원</a>
<a href="{RESULT_URL}">
 <span>{CARD_TITLE}</span>
 <img src="{IMG_URL}"/>
</a>
</body></html>'''


def evidence(query=TITLE):
    selected = select_similar_current(HTML, query)
    assert selected is not None
    return {**selected,
            "source_url": "https://fallcent.com/product/search/?keyword="
                          + "26%EB%85%84+%EC%8B%A0%EC%95%88",
           }


class FallcentSimilarCurrentTests(unittest.TestCase):
    def test_different_weight_accepted_without_exact_option_claim(self):
        row = evidence()
        self.assertEqual(row["observed_current_krw"], 17420)
        self.assertFalse(row["exact_item_verified"])
        self.assertEqual(valid_search_price(row, TITLE), 17420)
        self.assertIn("20kg", row["source_title"])
        self.assertIn("10kg", TITLE)
        self.assertEqual(row["result_url"], RESULT_URL)

    def test_never_accepts_other_family_ambiguous_prices_or_og_price(self):
        self.assertIsNone(select_similar_current(
            '<a href="https://fcnt.link/x">아이폰 케이스 50,000원</a>', TITLE))
        ambiguous = HTML.replace("17,420원", "17,420원 <del>22,000원</del>")
        self.assertIsNone(select_similar_current(ambiguous, TITLE))
        self.assertIsNone(select_similar_current(
            HTML.replace("17,420원", "할인율 20%"), TITLE))
        self.assertEqual(evidence()["observed_current_krw"], 17420)

    def test_suspected_provenance_tampering_is_rejected(self):
        baseline = evidence()
        edits = [
            {"observed_current_krw": 99999},
            {"exact_item_verified": True},
            {"source_url": "https://evil.example/product/search/?keyword=salt"},
            {"result_url": "https://fcnt.link.evil.example/salt"},
            {"source_title": "전혀 다른 상품 17,420원"},
            {"search_match_basis": "EXACT_ITEM_VERIFIED"},
            {"common_terms": ["irrelevant", "unmatched"]},
        ]
        for edit in edits:
            with self.subTest(edit=edit):
                self.assertIsNone(valid_search_price(
                    {**baseline, **edit}, TITLE))

    def test_public_query_fetch_is_read_only_with_no_redirect_or_product_click(self):
        calls = []
        def opener(req, timeout):
            calls.append(req.full_url)
            return FakeResponse(req.full_url, HTML.encode(), "text/html")
        r = fetch_similar_current(TITLE, opener=opener)
        self.assertEqual(r["status"], STATUS)
        self.assertEqual(r["observed_current_krw"], 17420)
        self.assertEqual(len(calls), 1)
        self.assertIn("/product/search/?keyword=", calls[0])
        self.assertEqual(valid_search_price(r, TITLE), 17420)

    def test_lowchart_403_then_similar_price_in_both_publisher_formats(self):
        def fallcent_photo(title, output, mid):
            self.assertEqual(title, TITLE)
            output.mkdir(parents=True, exist_ok=True)
            (output / f"jirum_{mid}_fallcent_photo.png").write_bytes(PNG)
            return {
                "status": "IMAGE_SAVED",
                "saved_name": f"jirum_{mid}_fallcent_photo.png",
                "bytes": len(PNG),
                "image_source": "fallcent_search_representative",
                "image_match_basis": "UNVERIFIED_FALLCENT_SEARCH_RESULT",
                "representative_fallcent_image": True,
                "image_provider_verified": False,
                "image_provider_title": CARD_TITLE,
            }
        with TemporaryDirectory() as td, \
             patch.dict(os.environ, {
                 "JIRUM_FALLCENT_SIMILAR_CURRENT_ENABLED": "true",
                 "JIRUM_IMAGE_SOURCE": "lowchart",
                 "COUPANG_PARTNER_LPTAG": "AF9668915",
             }), \
             patch("gh_only.default_deal_automation.lookup_exact_item_photo",
                   return_value={"status": "LOWCHART_IMAGE_PAGE_REQUEST_ERROR",
                                 "http_status": 403}):
            folder = Path(td)
            row = enrich_deal(
                DEAL, folder, resolver=lambda _: SALT_ID,
                history_fetch=lambda *args: {
                    "http_status": 403, "reason": "HTTP_ERROR",
                    "usable_historical_reference": False,
                },
                fallcent_lookup=fallcent_photo,
                fallcent_similar_current_lookup=lambda title: evidence(title),
                fallcent_high_lookup=lambda *args:
                    self.fail("similar current found, should not fetch exact high"),
                owner_tag="AF9668915",
            )
            self.assertEqual(row["comparison_price_source"],
                             "fallcent_similar_search_current")
            self.assertEqual(row["caption"]["comparison_reference_price_krw"], 17420)
            self.assertIsNone(row["caption"]["approx_discount_vs_reference_pct"])
            self.assertTrue(row["caption"]["comparison_is_similar_product_only"])
            self.assertIn("폴센트 유사 상품 현재가(참고) : 약 17,420원",
                          row["caption"]["caption"])
            self.assertIn("옵션 및 실제 구매가는 다를 수 있습니다.",
                          row["caption"]["caption"])
            self.assertNotIn("%할인", row["caption"]["caption"])
            self.assertNotIn("평소 판매가 : 약 17,420원", row["caption"]["caption"])
            (folder / "drafts.json").write_text(
                json.dumps({"schema": "github-jirum-default-price-photo-affsdp-draft-v2",
                            "drafts": [row]}, ensure_ascii=False),
                encoding="utf-8",
            )
            verified = validate_draft(folder, 96123)
            self.assertEqual(verified["source_id"], 96123)
            # The Naver formatter requires an API-issued URL; use only a fake
            # known-good URL (no external browser or social publishing).
            pub = {**verified, "candidate_url": "https://link.coupang.com/a/AbcDEf1234"}
            blocks = build_blocks(row, pub, DEAL)
            validate_price_presentation(blocks, row)
            display = "\n".join(b.get("text", "") for b in blocks)
            self.assertIn("폴센트 유사 상품 현재가(참고)", display)
            self.assertIn("옵션 및 실제 구매가가 다를 수 있습니다.", display)
            self.assertNotIn("[약 ", display)

            bad = copy.deepcopy(row)
            bad["caption"]["approx_discount_vs_reference_pct"] = 30
            (folder / "drafts.json").write_text(
                json.dumps({"schema": "github-jirum-default-price-photo-affsdp-draft-v2",
                            "drafts": [bad]}, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                # Earliest pre-submit guard rejects the fabricated discount
                # prefix before even reading the similar-price proof.
                validate_draft(folder, 96123)
            with self.assertRaisesRegex(ValueError,
                                         "NAVER_SIMILAR_PRODUCT_DISCOUNT_PROHIBITED"):
                build_blocks(bad, pub, DEAL)

    def test_formatter_does_not_misrepresent_reference_as_a_sale_discount(self):
        caption = create_default_preview(
            7900, "잔스포츠 하프 프린트 백팩",
            49000, 99101,
            reference_source="fallcent_similar_search_current",
        )
        self.assertIn("💸 폴센트 유사 상품 현재가(참고) : 약 49,000원",
                      caption["caption"])
        self.assertNotIn("80%할인", caption["caption"])
        self.assertIsNone(caption["actual_discount_vs_reference_pct"])
        self.assertIsNone(caption["actual_discount_vs_historical_mean_pct"])
        self.assertFalse(caption["comparison_price_is_historical_average"])


if __name__ == "__main__":
    unittest.main()
