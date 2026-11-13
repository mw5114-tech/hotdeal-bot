"""Offline tests for independent Coupang API radar. Never use live credentials."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from unittest import TestCase, main
from unittest.mock import patch

from api_radar import collector as r


def sample(**extra):
    obj = {
        "productId": 12345678, "productName": "브랜드 무선 키보드 저소음 사무용",
        "productPrice": 39900,
        "productImage": "https://thumbnail.coupangcdn.com/thumb/123.jpg",
        "productUrl": "https://link.coupang.com/a/Abcd12345",
        "isRocket": True, "isFreeShipping": True,
    }
    obj.update(extra)
    return obj


class Response:
    status = 200
    def __init__(self, obj):
        self.body = json.dumps(obj).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.body[:size]


class RadarTests(TestCase):
    def test_signature_has_query(self):
        date = datetime(2026, 11, 13, tzinfo=timezone.utc)
        path = r.SEARCH + "?keyword=ssd&limit=10"
        got = r.signed_header("a", "s", path, date)
        expected = hmac.new(b"s", ("261113T000000ZGET" + path).encode(),
                            hashlib.sha256).hexdigest()
        self.assertIn("signature=" + expected, got)
        self.assertNotIn("secret-key", got)

    def test_only_allow_known_api_paths(self):
        with self.assertRaisesRegex(r.RadarApiError, "API_PATH_NOT_ALLOWED"):
            r.get_api("/not-affiliate")
        with self.assertRaisesRegex(r.RadarApiError, "API_SEARCH_QUERY_INVALID"):
            r.get_api(r.SEARCH, {"keyword": "", "limit": 100})

    def test_successful_products_shape(self):
        with patch.dict(os.environ, {"COUPANG_PARTNERS_ACCESS_KEY": "fake",
                                     "COUPANG_PARTNERS_SECRET_KEY": "fake"}):
            obj = r.get_api(r.GOLDBOX, opener=lambda req, timeout:
                            Response({"rCode": "0", "data": [sample()]}))
        rows = r.extract_rows(obj)
        self.assertEqual(rows[0]["productName"], sample()["productName"])
        self.assertEqual(r.normalize(rows[0], "goldbox")["price"], 39900)

    def test_nested_search_products(self):
        out = r.extract_rows({"rCode": 0, "data": {"productData": [sample()]}})
        self.assertEqual(len(out), 1)

    def test_reject_non_coupang_images(self):
        self.assertIsNone(r.normalize(sample(productImage="https://evil.example/p.png"), "goldbox"))
        self.assertIsNone(r.normalize(sample(productUrl="http://link.coupang.com/a/a"), "goldbox"))

    def test_title_and_price_filter(self):
        self.assertIsNone(r.normalize(sample(productName="중고 랜덤 키보드"), "search"))
        self.assertIsNone(r.normalize(sample(productPrice=0), "search"))

    def test_unique_goldbox_wins(self):
        p1 = r.normalize(sample(), "search")
        p2 = r.normalize(sample(), "goldbox")
        self.assertEqual(r.unique_products([p1, p2])[0]["source"], "goldbox")

    def test_no_discount_claim_from_one_price(self):
        state = r.empty_state()
        product = r.normalize(sample(), "goldbox")
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        saved, candidates = r.update_state(state, [product], now)
        self.assertIsNone(candidates[0]["product_id_price_drop_pct"])
        self.assertFalse(candidates[0]["posting_eligible"])
        self.assertEqual(len(saved["products"][product["id"]]["history"]), 1)

    def test_price_change_does_not_authorize_post(self):
        old = r.empty_state()
        old["products"]["12345678"] = {"history": [
            {"date": f"2026-11-{d:02d}", "price": 50000}
            for d in (1, 3, 5, 7, 9, 11)]}
        candidate = r.normalize(sample(productPrice=30000), "goldbox")
        _, ranked = r.update_state(old, [candidate], datetime(2026, 11, 13, tzinfo=timezone.utc))
        self.assertEqual(ranked[0]["product_id_price_drop_pct"], 40.0)
        self.assertFalse(ranked[0]["posting_eligible"])

    def test_output_does_not_contain_affiliate_credential(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            r.write_report(Path(td), datetime.now(timezone.utc),
                           [r.normalize(sample(), "goldbox")], [], True)
            raw = (Path(td) / "summary.json").read_text()
            self.assertNotIn("Authorization", raw)
            self.assertNotIn("productImage", raw)
            self.assertNotIn("productUrl", raw)
            self.assertIn('"posting_enabled": false', raw)


if __name__ == "__main__":
    main()
