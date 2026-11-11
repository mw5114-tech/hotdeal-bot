"""Offline contract tests for the official Coupang Partners deeplink client."""
import hashlib
import hmac
import io
import json
import unittest
from unittest.mock import patch
from urllib import error

from gh_only.coupang_partners_api import (
    HOST, PATH, CoupangPartnersError, SUBID, _validate_response,
    authorization, create_product_deeplink, issued_link_url, request_deeplink
)

IDENTITY = {"status": "EXACT_ITEM_IDENTIFIED", "product_id": "12345678",
            "item_id": "45678901", "vendor_item_id": "90123456"}
CANONICAL = ("https://www.coupang.com/vp/products/12345678"
             "?itemId=45678901&vendorItemId=90123456")
OFFICIAL = "https://link.coupang.com/a/AbcDEf1234"

class Response:
    status = 200
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n): return self.data[:n]

def success(original=CANONICAL, issued=OFFICIAL, landing=None):
    return {"rCode": "0", "rMessage": "",
            "data": [{"originalUrl": original, "shortenUrl": issued,
                      **({"landingUrl": landing} if landing is not None else {})}]}

class OfficialApiTests(unittest.TestCase):
    def test_hmac_matches_official_signature_algorithm(self):
        date = "261111T010203Z"
        auth = authorization("access-key", "secret-key", date)
        digest = hmac.new(b"secret-key", (date+"POST"+PATH).encode(),
                          hashlib.sha256).hexdigest()
        self.assertEqual(auth,
            "CEA algorithm=HmacSHA256, access-key=access-key, signed-date="
            + date + ", signature=" + digest)

    def test_only_issued_shortlinks_allowed(self):
        for link in (OFFICIAL, "https://coupa.ng/AbcDEf1234"):
            self.assertTrue(issued_link_url(link))
        for link in (
            "https://evil.example/a/AbcDEf1234",
            "http://link.coupang.com/a/AbcDEf1234",
            "https://link.coupang.com/re/AFFSDP?lptag=AF9668915",
            "https://link.coupang.com/a/AbcDEf1234?subid=bad",
            "https://user@link.coupang.com/a/AbcDEf1234",
            "https://coupa.ng.evil.example/AbcDEf1234",
            "https://coupa.ng/AbcDEf1234/evil",
            "https://link.coupang.com/a/AbcDEf1234#fragment",
        ):
            with self.subTest(link=link):
                self.assertFalse(issued_link_url(link))

    def test_real_api_shape_and_subid_without_leaking_secret(self):
        captured = {}
        def call(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            captured["headers"] = dict(req.header_items())
            captured["timeout"] = timeout
            return Response(success())
        with patch.dict("os.environ", {
            "COUPANG_PARTNERS_ACCESS_KEY": "access",
            "COUPANG_PARTNERS_SECRET_KEY": "secret",
        }), patch("gh_only.coupang_partners_api.request.urlopen",
                  side_effect=call) as send:
            result = create_product_deeplink(IDENTITY)
        self.assertEqual(result["partners_link"], OFFICIAL)
        self.assertTrue(result["official_link_issued"])
        self.assertFalse(result["commission_attribution_verified"])
        self.assertEqual(result["subid_requested"], SUBID)
        self.assertEqual(captured["body"],
                         {"coupangUrls": [CANONICAL], "subId": "naverblog"})
        self.assertEqual(captured["url"], HOST+PATH)
        self.assertEqual(send.call_count, 1)
        self.assertIn("CEA algorithm=HmacSHA256", captured["headers"]["Authorization"])

    def test_missing_keys_never_calls_api(self):
        with patch.dict("os.environ", {
            "COUPANG_PARTNERS_ACCESS_KEY": "",
            "COUPANG_PARTNERS_SECRET_KEY": "",
        }), patch("gh_only.coupang_partners_api.request.urlopen") as send:
            with self.assertRaisesRegex(CoupangPartnersError, "KEYS_MISSING"):
                create_product_deeplink(IDENTITY)
        send.assert_not_called()

    def test_unverified_identity_does_not_call_api(self):
        with patch("gh_only.coupang_partners_api.request.urlopen") as send:
            with self.assertRaisesRegex(CoupangPartnersError, "IDENTITY_INVALID"):
                create_product_deeplink({**IDENTITY, "status": "UNVERIFIED"})
        send.assert_not_called()

    def test_response_must_match_exact_requested_url(self):
        with self.assertRaisesRegex(CoupangPartnersError, "ORIGINAL_URL_MISMATCH"):
            _validate_response(success(original="https://www.coupang.com/np/search?q=wrong"),
                               CANONICAL, IDENTITY)

    def test_response_rejects_foreign_item_landing(self):
        with self.assertRaisesRegex(CoupangPartnersError, "LANDING_ITEM_MISMATCH"):
            _validate_response(success(landing="https://www.coupang.com/vp/products/12345678?itemId=99999999"),
                               CANONICAL, IDENTITY)

    def test_response_matching_option_landing_accepted(self):
        link = _validate_response(success(landing=CANONICAL + "&isAddedCart="),
                                  CANONICAL, IDENTITY)
        self.assertEqual(link, OFFICIAL)

    def test_nonzero_error_and_missing_link_fail_closed(self):
        with self.assertRaisesRegex(CoupangPartnersError, "NON_SUCCESS"):
            _validate_response({"rCode": "403", "data": []}, CANONICAL, IDENTITY)
        with self.assertRaisesRegex(CoupangPartnersError, "RESULT_NOT_UNIQUE"):
            _validate_response({"rCode": "0", "data": []}, CANONICAL, IDENTITY)
        with self.assertRaisesRegex(CoupangPartnersError, "ISSUED_URL_INVALID"):
            _validate_response(success(issued="https://evil.test/a/AbcDEf1234"),
                               CANONICAL, IDENTITY)

    def test_http_error_does_not_leak_body_or_credentials(self):
        ex = error.HTTPError(HOST+PATH, 403, "forbidden", {}, io.BytesIO(b"token=SECRET"))
        with patch.dict("os.environ", {
            "COUPANG_PARTNERS_ACCESS_KEY": "access",
            "COUPANG_PARTNERS_SECRET_KEY": "secret",
        }), patch("gh_only.coupang_partners_api.request.urlopen", side_effect=ex):
            with self.assertRaisesRegex(CoupangPartnersError, "COUPANG_API_HTTP_403") as got:
                create_product_deeplink(IDENTITY)
        self.assertNotIn("SECRET", str(got.exception))
        self.assertNotIn("access", str(got.exception))


if __name__ == "__main__":
    unittest.main()
