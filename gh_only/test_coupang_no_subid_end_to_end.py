"""No-network regression: both owner channels use untouched no-subId API links."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gh_only.jirum_manual_queue import build_next, ingest
from gh_only.test_jirum_manual_queue import baseline, deal
from gh_only.naver_blog_jirum import apply_official_api_link, validate_naver_link_subid
from gh_only.daangn_editor_probe import read_one_draft
from gh_only.coupang_partners_api import CoupangPartnersError


IDENTITY = {
    "status": "EXACT_ITEM_IDENTIFIED",
    "product_id": "12345678",
    "item_id": "45678901",
    "vendor_item_id": "90123456",
}
SHORT = "https://link.coupang.com/a/AbcDEf1234"
UNISSUED = (
    "https://link.coupang.com/re/AFFSDP?"
    "lptag=AF9668915&pageKey=12345678&itemId=45678901"
)


def draft():
    return {
        "product_identity": IDENTITY.copy(),
        "composed_affiliate_url": UNISSUED,
        "caption": {"caption": "상품 링크: " + UNISSUED},
    }


def fake_api_receipt(*args, **kwargs):
    if args != (IDENTITY,) or kwargs:
        raise AssertionError("CHANNEL_MUST_NOT_PASS_CUSTOM_SUBID")
    return {
        "status": "OFFICIAL_COUPANG_PARTNERS_API",
        "partners_link": SHORT,
        "product_id": IDENTITY["product_id"],
        "item_id": IDENTITY["item_id"],
        "vendor_item_id": IDENTITY["vendor_item_id"],
        "subid_requested": None,
        "official_link_issued": True,
        "commission_attribution_verified": False,
    }


class NoSubIdChannelTests(unittest.TestCase):
    def test_naver_uses_api_original_shorten_url_and_no_custom_subid(self):
        d = draft()
        with patch("gh_only.coupang_partners_api.create_product_deeplink",
                   side_effect=fake_api_receipt) as api:
            result = apply_official_api_link(d, 99901)
        api.assert_called_once_with(IDENTITY)
        self.assertEqual(result["composed_affiliate_url"], SHORT)
        self.assertEqual(result["official_link_receipt"]["subid_requested"], None)
        self.assertEqual(result["caption"]["caption"], "상품 링크: " + SHORT)
        self.assertIsNone(validate_naver_link_subid(SHORT, result["official_link_receipt"]))

    def test_daangn_uses_api_original_shorten_url_and_no_custom_subid(self):
        s, _, _ = ingest(baseline(), [deal(94744)])
        candidate = draft()
        with TemporaryDirectory() as td:
            env = {
                "DAANGN_OFFICIAL_API_LINK_ENABLED": "true",
                "JIRUM_REQUIRE_OFFICIAL_PARTNERS_LINK": "false",
                "JIRUM_PUBLISH_BEFORE_MUSE_REWRITE": "false",
                "COUPANG_PARTNER_SUBID": "",
                "COUPANG_PARTNER_LPTAG": "AF9668915",
            }
            with patch.dict(os.environ, env), \
                 patch("gh_only.default_deal_automation.enrich_deal",return_value=candidate), \
                 patch("gh_only.coupang_partners_api.create_product_deeplink",
                       side_effect=fake_api_receipt) as api, \
                 patch("gh_only.jirum_manual_queue.DRAFT_DIR",Path(td)), \
                 patch("gh_only.jirum_manual_queue.validate_draft",
                       return_value={"candidate_url": SHORT}):
                result = build_next(s)
            api.assert_called_once_with(IDENTITY)
            self.assertEqual(result["candidate_url"], SHORT)
            saved = json.loads((Path(td)/"drafts.json").read_text(encoding="utf-8"))["drafts"][0]
        self.assertEqual(saved["official_link_receipt"]["subid_requested"], None)
        self.assertEqual(saved["official_link_receipt"]["partners_link"], SHORT)
        self.assertEqual(saved["caption"]["caption"], "상품 링크: "+SHORT)
        self.assertNotIn("AFFSDP", saved["caption"]["caption"])
        self.assertFalse(saved["affsdp"]["commission_attribution_verified"])

    def test_naver_refuses_legacy_channel_subid_receipt(self):
        d = draft()
        with patch("gh_only.coupang_partners_api.create_product_deeplink",
                   return_value={**fake_api_receipt(IDENTITY), "subid_requested": "naverblog"}):
            with self.assertRaisesRegex(RuntimeError, "SUBID_MISMATCH"):
                apply_official_api_link(d, 99901)
        self.assertNotIn("official_link_receipt", d)


if __name__ == "__main__":
    unittest.main()
