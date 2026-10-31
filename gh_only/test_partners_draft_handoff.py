"""Offline Jirum evidence -> personal-link request contracts."""
import copy
import unittest
from gh_only.partners_draft_handoff import request_from_report

def report():
    return {
        "schema":"github-jirum-default-price-photo-v1",
        "drafts":[{
            "status":"DRAFT_ONLY_DO_NOT_PUBLISH",
            "kakao_product_match":True,
            "personal_affiliate_link_created":False,
            "source_message_id":94718,
            "product_identity":{
                "status":"EXACT_ITEM_IDENTIFIED",
                "product_id":"9217210892",
                "item_id":"27232840915",
                "vendor_item_id":"94199974724"
            }
        }]
    }

class Tests(unittest.TestCase):
    def test_verified_exact_option_packet(self):
        p=request_from_report(report())
        self.assertEqual(p["schema"],"jirum-partners-one-link-request-v1")
        self.assertEqual(p["status"],"READY_FOR_AUTHENTICATED_PARTNERS_UI")
        self.assertEqual(p["source_message_id"],94718)
        self.assertEqual(p["canonical_product_url"],
            "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724")
        self.assertFalse(p["personal_affiliate_link_generated"])
        self.assertEqual(p["publish_count"],0)
        self.assertEqual(p["request_id"],request_from_report(report())["request_id"])

    def test_option_change_alters_request_id(self):
        a=request_from_report(report())
        b=report()
        b["drafts"][0]["product_identity"]["item_id"]="27232840916"
        self.assertNotEqual(a["request_id"],request_from_report(b)["request_id"])

    def test_missing_og_product_match_never_requests(self):
        p=report()
        p["drafts"][0]["kakao_product_match"]=False
        with self.assertRaisesRegex(ValueError,"OG_PRODUCT_ID_NOT_CONFIRMED"):
            request_from_report(p)

    def test_wrong_or_missing_identity_never_requests(self):
        p=report()
        p["drafts"][0]["product_identity"]["status"]="UNRESOLVED"
        with self.assertRaisesRegex(ValueError,"EXACT_ITEM_NOT_VERIFIED"):
            request_from_report(p)
        p=report()
        p["drafts"][0]["product_identity"]["vendor_item_id"]="abc"
        with self.assertRaisesRegex(ValueError,"INCOMPLETE_PRODUCT_ID"):
            request_from_report(p)

    def test_not_a_draft_or_duplicate_claim_never_requests(self):
        p=report()
        p["drafts"][0]["status"]="POSTED"
        with self.assertRaisesRegex(ValueError,"UNVERIFIED_DRAFT_STATUS"):
            request_from_report(p)
        p=report()
        p["drafts"][0]["personal_affiliate_link_created"]=True
        with self.assertRaisesRegex(ValueError,"AFFILIATE_LINK_ALREADY_CLAIMED"):
            request_from_report(p)

    def test_many_drafts_never_guesses(self):
        p=report()
        p["drafts"].append(copy.deepcopy(p["drafts"][0]))
        with self.assertRaisesRegex(ValueError,"EXACTLY_ONE_DRAFT_REQUIRED"):
            request_from_report(p)

    def test_no_valid_message_id(self):
        p=report()
        p["drafts"][0]["source_message_id"]=False
        with self.assertRaisesRegex(ValueError,"INVALID_SOURCE_MESSAGE_ID"):
            request_from_report(p)

if __name__ == "__main__":
    unittest.main()
