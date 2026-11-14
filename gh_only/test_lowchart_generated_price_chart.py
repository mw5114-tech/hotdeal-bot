"""Offline regression tests for ORIGINAL lowchart summary chart (no fake series)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gh_only.lowchart_generated_price_chart import (
    _model, _svg, generate_price_chart_image,
)
from gh_only.daangn_editor_probe import _verified_lowchart_chart_identity

ROW = {
    "product_identity": {
        "product_id": "7098352204", "item_id": "19148341413",
    },
    "caption": {
        "comparison_reference_source": "lowchart_public_history",
        "comparison_reference_price_krw": 21858,
        "sale_price_krw": 19990,
    },
    "history": {
        "visible_exact_item_prices_verified": True,
        "source_item_identifiers_present": True,
        "product_id": "7098352204", "item_id": "19148341413",
        "source_url": "https://www.lowchart.com/7098352204-19148341413",
        "history_window": "recent_3_weeks",
        "observed_low_krw": 17190,
        "observed_high_krw": 26500,
        "observed_current_krw": 19990,
        "observed_average_krw": 21858,
    },
}


class TestOriginalChart(unittest.TestCase):
    def test_verified_exact_item_renders_true_summary(self):
        chart = _verified_lowchart_chart_identity(copy.deepcopy(ROW))
        model = _model(chart)
        self.assertEqual(model["avg"], 21858)
        self.assertEqual(model["low"], 17190)
        self.assertEqual(model["deal"], 19990)
        self.assertEqual(model["average_caption"], "RECENT 3-WEEK AVG")
        svg = _svg(model)
        self.assertIn("RECENT 3-WEEK AVG", svg)
        self.assertIn("21,858 KRW", svg)
        self.assertIn("19,990 KRW", svg)
        self.assertIn("not a timeline", svg)
        self.assertNotIn("2026-10-01", svg)
        self.assertIn("DEAL IS", svg)

    def test_history_not_public_prevents_fabricated_series(self):
        row = copy.deepcopy(ROW)
        row["history"]["observed_average_krw"] = None
        self.assertIsNone(_model(_verified_lowchart_chart_identity(row)))
        row = copy.deepcopy(ROW)
        row["history"]["observed_average_krw"] = 999999
        self.assertIsNone(_model(_verified_lowchart_chart_identity(row)))
        row = copy.deepcopy(ROW)
        row["history"]["item_id"] = "19148341414"
        self.assertIsNone(_verified_lowchart_chart_identity(row))

    def test_fallcent_basis_does_not_claim_lowchart_chart(self):
        row = copy.deepcopy(ROW)
        row["caption"]["comparison_reference_source"] = "fallcent_observed_high"
        self.assertIsNone(_verified_lowchart_chart_identity(row))

    def test_each_new_item_is_distinct(self):
        row1 = copy.deepcopy(ROW)
        row2 = copy.deepcopy(ROW)
        row2["product_identity"]["product_id"] = "1487703230"
        row2["product_identity"]["item_id"] = "2554295884"
        row2["history"]["product_id"] = "1487703230"
        row2["history"]["item_id"] = "2554295884"
        row2["history"]["source_url"] = "https://www.lowchart.com/1487703230-2554295884"
        row2["history"]["observed_low_krw"] = 12250
        row2["history"]["observed_high_krw"] = 28000
        row2["history"]["observed_average_krw"] = 19190
        row2["history"]["observed_current_krw"] = 17880
        row2["caption"]["comparison_reference_price_krw"] = 19190
        row2["caption"]["sale_price_krw"] = 14120
        m1 = _model(_verified_lowchart_chart_identity(row1))
        m2 = _model(_verified_lowchart_chart_identity(row2))
        self.assertNotEqual(m1["product_id"], m2["product_id"])
        self.assertNotEqual(m1["item_id"], m2["item_id"])
        self.assertNotEqual(_svg(m1), _svg(m2))

    def test_skips_when_disabled_or_invalid_without_browser(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"JIRUM_LOWCHART_CHART_MODE": "off"}):
                self.assertIsNone(generate_price_chart_image(
                    None, _verified_lowchart_chart_identity(ROW),
                    Path(directory), 95999))
            with patch.dict(os.environ, {"JIRUM_LOWCHART_CHART_MODE": "generated"}):
                self.assertIsNone(generate_price_chart_image(
                    None, {"product_id": "broken"}, Path(directory), 95999))

    def test_disallow_price_reference_substitution(self):
        chart = _verified_lowchart_chart_identity(copy.deepcopy(ROW))
        chart["comparison_reference_price_krw"] += 1
        self.assertIsNone(_model(chart))
        chart = _verified_lowchart_chart_identity(copy.deepcopy(ROW))
        chart["comparison_reference_source"] = "lowchart_observed_high"
        chart["comparison_reference_price_krw"] = chart["observed_high_krw"]
        self.assertIsNotNone(_model(chart))


if __name__ == "__main__":
    unittest.main()
