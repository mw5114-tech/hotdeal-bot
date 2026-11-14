"""Offline tests for optional exact-item Lowchart chart screenshots."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

from gh_only.lowchart_chart_capture import _exact_identity, chart_bounds, capture_chart_image
from gh_only.naver_blog_format import add_price_chart_block, validate_blocks, DISCLOSURE
from gh_only.daangn_editor_probe import _verified_lowchart_chart_identity


class FakePage:
    def evaluate(self, script):
        assert "차트 퍼가기" in script
        return {"x": 100, "y": 200, "width": 750, "height": 460}


class ChartUnitTests(TestCase):
    def test_identity_requires_exact_ids(self):
        self.assertEqual(
            _exact_identity({"product_id": "8479163070", "item_id": "24536044974"}),
            ("8479163070", "24536044974"))
        self.assertIsNone(_exact_identity({"product_id": "bad", "item_id": "24536044974"}))
        self.assertIsNone(_exact_identity({"product_id": "8479163070", "item_id": None}))
        self.assertIsNone(_exact_identity(None))

    def test_price_and_chart_must_be_same_item_and_lowchart_source(self):
        """Each next deal carries its own verified product+option, no static ID."""
        for p, item in (
            ("8479163070", "24536044974"),
            ("6221369038", "12453073752"),
        ):
            row = {
                "product_identity": {"product_id": p, "item_id": item},
                "caption": {
                    "comparison_reference_source": "lowchart_public_history",
                    "comparison_reference_price_krw": 10000,
                    "sale_price_krw": 8500,
                },
                "history": {
                    "visible_exact_item_prices_verified": True,
                    "source_item_identifiers_present": True,
                    "product_id": p, "item_id": item,
                    "source_url": f"https://www.lowchart.com/{p}-{item}",
                    "observed_low_krw": 8000, "observed_high_krw": 12000,
                    "observed_current_krw": 9000, "observed_average_krw": 10000,
                    "history_window": "recent_3_weeks",
                },
            }
            chart = _verified_lowchart_chart_identity(row)
            self.assertIsNotNone(chart)
            self.assertEqual((chart["product_id"], chart["item_id"]), (p, item))
            self.assertEqual(chart["observed_average_krw"], 10000)
            self.assertEqual(chart["deal_price_krw"], 8500)
            row["caption"]["comparison_reference_source"] = "fallcent_observed_high"
            self.assertIsNone(_verified_lowchart_chart_identity(row))
            row["caption"]["comparison_reference_source"] = "lowchart_observed_high"
            row["history"]["item_id"] = "98765432123"
            self.assertIsNone(_verified_lowchart_chart_identity(row))

    def test_capture_off_by_default_and_does_not_open_browser(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "JIRUM_LOWCHART_CHART_IMAGE_ENABLED": "false",
        }):
            self.assertIsNone(capture_chart_image(
                None, {"product_id": "8479163070", "item_id": "24536044974"},
                Path(folder), 95712,
            ))

    def test_visible_chart_clip(self):
        self.assertEqual(chart_bounds(FakePage())["width"], 750)

    def test_naver_chart_position_and_validation(self):
        blocks = [
            {"type": "text", "text": DISCLOSURE, "kind": "disclosure"},
            {"type": "image", "kind": "hero_photo"},
            {"type": "text", "text": "🛍️ 핫딜", "kind": "heading"},
            {"type": "text", "text": "상품명", "kind": "product"},
            {"type": "text", "text": "💸 5,000원", "kind": "reference"},
            {"type": "text", "text": "🔥 오늘 특가 3,000원", "kind": "price"},
            {"type": "text", "text": "※ 안내", "kind": "note"},
            {"type": "text", "text": "📊 가격 비교 기준", "kind": "source_basis"},
            {"type": "text", "text": "📦 쿠팡에서 상품정보 더보기",
             "kind": "affiliate_link", "link_url": "https://link.coupang.com/a/AbCd123"},
        ]
        self.assertTrue(add_price_chart_block(blocks))
        self.assertFalse(add_price_chart_block(blocks))
        self.assertEqual(blocks[8], {"type": "image", "kind": "price_chart"})
        validate_blocks(blocks, "https://link.coupang.com/a/AbCd123")
        del blocks[8]
        validate_blocks(blocks, "https://link.coupang.com/a/AbCd123")


if __name__ == "__main__":
    main()
