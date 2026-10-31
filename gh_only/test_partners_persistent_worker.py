"""Offline regression checks: no browser, network, cookies or credentials."""
import unittest
from gh_only.partners_draft_handoff import request_from_report
from unittest import mock
from gh_only import partners_persistent_worker as worker

PRODUCT="https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724"
GOOD_LINK="https://link.coupang.com/a/TESTown123"

def resolved_same(_):
    return {"result":"DIRECT_PRODUCT_ID_CANDIDATE",
            "product_id":"9217210892", "item_id":"27232840915",
            "vendor_item_id":"94199974724"}

def resolved_wrong(_):
    return {"result":"DIRECT_PRODUCT_ID_CANDIDATE",
            "product_id":"9217210892", "item_id":"00000",
            "vendor_item_id":"94199974724"}

class Input:
    def __init__(self, value=""):
        self.value=value;self.fill_count=0
    def count(self):return 1
    def is_visible(self):return True
    def fill(self, value,timeout=None):
        self.value=value;self.fill_count+=1
    def input_value(self):return self.value

class Button:
    def __init__(self):self.clicks=0
    def count(self):return 1
    def is_visible(self):return True
    def is_enabled(self):return True
    def click(self,timeout=None):self.clicks+=1

class Page:
    def __init__(self,links=None):
        self.url=worker.PARTNERS_URL
        self.field=Input()
        self.button=Button()
        self.links=list(links if links is not None else [set(),{GOOD_LINK}])
        self.calls=0
    def locator(self,selector):
        if selector!="input#url":raise AssertionError(selector)
        return self.field
    def get_by_role(self,role,name=None):
        if role!="button":raise AssertionError(role)
        return self.button
    def evaluate(self,code):
        state=self.links[min(self.calls,len(self.links)-1)]
        self.calls+=1
        return " ".join(state)

class Tests(unittest.TestCase):
    def test_exact_identity(self):
        self.assertEqual(worker.canonical_identity(PRODUCT),(
            "9217210892","27232840915","94199974724"))
        self.assertTrue(worker.matching_result_link(GOOD_LINK,PRODUCT,resolved_same))
        self.assertFalse(worker.matching_result_link(GOOD_LINK,PRODUCT,resolved_wrong))
        self.assertFalse(worker.matching_result_link("http://evil.invalid/a/test",PRODUCT,resolved_same))

    def test_fixture_success_one_click(self):
        page=Page()
        r=worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(r["status"],"NEW_LINK_EXACT_OPTION_VERIFIED")
        self.assertEqual(r["clicks"],1)
        self.assertFalse(r["account_owner_independently_verified"])
        self.assertEqual(page.field.fill_count,1)
        self.assertEqual(page.button.clicks,1)

    def test_wrong_product_blocks_output(self):
        page=Page()
        r=worker.create_once(page,PRODUCT,resolved_wrong,sleeper=lambda _:None)
        self.assertEqual(r["status"],"NEW_LINK_ITEM_NOT_VERIFIED")
        self.assertEqual(page.button.clicks,1)
        self.assertNotIn("generated_link",r)

    def test_no_new_link_never_retry(self):
        page=Page([set(),set()])
        r=worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(r["status"],"LINK_RESULT_UNCERTAIN_NO_RETRY")
        self.assertEqual(page.button.clicks,1)

    def test_ambiguous_new_links_never_return_arbitrary_one(self):
        page=Page([set(),{GOOD_LINK,"https://link.coupang.com/a/OTHER"}])
        r=worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(r["status"],"MULTIPLE_NEW_LINKS")
        self.assertNotIn("generated_link",r)
        self.assertEqual(page.button.clicks,1)

    def test_old_result_not_falsely_credited_to_new_click(self):
        page=Page([{GOOD_LINK},{GOOD_LINK}])
        r=worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(r["status"],"LINK_RESULT_UNCERTAIN_NO_RETRY")
        self.assertEqual(page.button.clicks,1)

    def test_missing_form_blocks_click(self):
        page=Page()
        page.url="https://login.coupang.com/login"
        with self.assertRaisesRegex(RuntimeError,"NOT_ON_PARTNERS_PAGE"):
            worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(page.button.clicks,0)

    def test_broken_input_or_disabled_button_blocks_click(self):
        page=Page()
        page.field.is_visible=lambda:False
        with self.assertRaisesRegex(RuntimeError,"NEEDS_LOGIN_OR_FORM_CHANGED"):
            worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(page.button.clicks,0)
        page=Page()
        page.button.is_enabled=lambda:False
        with self.assertRaisesRegex(RuntimeError,"GENERATE_BUTTON_UNCONFIRMED"):
            worker.create_once(page,PRODUCT,resolved_same,sleeper=lambda _:None)
        self.assertEqual(page.button.clicks,0)

    def test_live_mode_requires_existing_profile_and_opt_in(self):
        from pathlib import Path
        with self.assertRaisesRegex(ValueError,"ABSOLUTE_PROFILE_DIRECTORY_REQUIRED"):
            worker.run_live("read-only",None,Path("relative/profile"))
        with self.assertRaisesRegex(ValueError,"DEDICATED_PROFILE_NOT_INITIALIZED"):
            worker.run_live("read-only",None,Path("/nonexistent/dedicated/profile"))
        with mock.patch.dict("os.environ",{"GH_ONLY_PARTNERS_GENERATE_ENABLED":"false"}):
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as profile:
                with self.assertRaisesRegex(ValueError,"EXPLICIT_GENERATE_OPT_IN_REQUIRED"):
                    worker.run_live("generate-one",PRODUCT,Path(profile))

    def test_request_packet_integrity(self):
        report={
            "schema":"github-jirum-default-price-photo-v1",
            "drafts":[{
                "status":"DRAFT_ONLY_DO_NOT_PUBLISH","kakao_product_match":True,
                "personal_affiliate_link_created":False,
                "source_message_id":94718,
                "product_identity":{
                    "status":"EXACT_ITEM_IDENTIFIED",
                    "product_id":"9217210892","item_id":"27232840915",
                    "vendor_item_id":"94199974724",
                }
            }]
        }
        packet=request_from_report(report)
        url, ident=worker.validate_request_packet(packet)
        self.assertEqual(url,PRODUCT)
        self.assertEqual(ident,packet["request_id"])
        packet["canonical_product_url"]=PRODUCT.replace("27232840915","000")
        with self.assertRaisesRegex(ValueError,"REQUEST_ID_MISMATCH"):
            worker.validate_request_packet(packet)

    def test_reject_unready_packet(self):
        with self.assertRaisesRegex(ValueError,"INVALID_REQUEST_PACKET"):
            worker.validate_request_packet({})
        with self.assertRaisesRegex(ValueError,"REQUEST_NOT_READY"):
            worker.validate_request_packet({
                "schema":"jirum-partners-one-link-request-v1",
                "status":"ALREADY_USED"
            })

if __name__=="__main__":
    unittest.main()
