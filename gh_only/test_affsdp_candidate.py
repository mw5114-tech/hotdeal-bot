"""Offline contracts for unissued AFFSDP composition, no network requests."""
import unittest
from urllib.parse import parse_qs, urlsplit

from gh_only.affsdp_candidate import compose_affsdp_candidate


class AffsdpCandidateTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "status": "EXACT_ITEM_IDENTIFIED",
            "product_id": "9217210892",
            "item_id": "27232840915",
            "vendor_item_id": "94199974724",
            "url_kind": "coupang_shortlink",
        }

    def test_exact_product_item_vendor_and_owner_tag(self):
        out = compose_affsdp_candidate(self.identity, "AF9668915")
        self.assertEqual(out["status"], "UNISSUED_AFFSDP_CANDIDATE")
        self.assertEqual(out["candidate_url"],
                         "https://link.coupang.com/re/AFFSDP?"
                         "lptag=AF9668915&pageKey=9217210892&itemId=27232840915"
                         "&vendorItemId=94199974724")
        self.assertFalse(out["official_link_issued"])
        self.assertFalse(out["commission_attribution_verified"])
        self.assertFalse(out["ready_for_automatic_publication"])

    def test_missing_vendor_never_invents_a_vendor(self):
        identity = {**self.identity, "vendor_item_id": None}
        out = compose_affsdp_candidate(identity, "AF9668915")
        self.assertEqual(out["status"], "UNISSUED_AFFSDP_CANDIDATE")
        qs = parse_qs(urlsplit(out["candidate_url"]).query)
        self.assertEqual(qs["pageKey"], ["9217210892"])
        self.assertEqual(qs["itemId"], ["27232840915"])
        self.assertNotIn("vendorItemId", qs)
        self.assertFalse(out["vendor_item_id_included"])

    def test_unidentified_shortlink_never_uses_opaque_code(self):
        out = compose_affsdp_candidate(
            {"status": "SHORTLINK_ERROR", "product_id": "123", "item_id": "456"},
            "AF9668915",
        )
        self.assertEqual(out["status"], "SOURCE_PRODUCT_ITEM_NOT_IDENTIFIED")
        self.assertIsNone(out["candidate_url"])

    def test_invalid_field_values_rejected(self):
        for changes in (
            {"product_id": "1&itemId=9"},
            {"item_id": "0"},
            {"vendor_item_id": "1&lptag=AF1234567"},
            {"vendor_item_id": "ㅁ"},
            {"item_id": None},
        ):
            with self.subTest(changes=changes):
                out = compose_affsdp_candidate({**self.identity, **changes}, "AF9668915")
                self.assertIsNone(out["candidate_url"])
                self.assertFalse(out["commission_attribution_verified"])

    def test_user_tag_validated_independently(self):
        for tag in ("", "AFabc", "AF111", "AF9668915&campaign=evil", "AF9668915\n"):
            with self.subTest(tag=tag):
                out = compose_affsdp_candidate(self.identity, tag)
                self.assertEqual(out["status"], "INVALID_OWNER_LPTAG")
                self.assertIsNone(out["candidate_url"])

    def test_never_claims_commission_without_real_issuance(self):
        out = compose_affsdp_candidate(self.identity, "AF9668915")
        self.assertNotIn("link.coupang.com/a/", out["candidate_url"])
        self.assertFalse(out["seller_option_independently_verified"])


if __name__ == "__main__":
    unittest.main()
