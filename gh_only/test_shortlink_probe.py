"""No-network shortlink tests."""
import unittest
from gh_only.shortlink_probe import one_hop


class FakeResponse:
    def __init__(self, status, target=None):
        self.status = status
        self.headers = {"Location": target} if target else {}


class ShortlinkTests(unittest.TestCase):
    def test_direct_product_id_and_item_id_without_following_target(self):
        calls = []
        def open_fn(req, timeout):
            calls.append((req.full_url, timeout))
            return FakeResponse(302,
                "https://www.coupang.com/vp/products/9412434217?itemId=25858815414&lptag=FOREIGN")
        result = one_hop("https://link.coupang.com/a/iiCmQxWdUG", opener=open_fn)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["product_id"], "9412434217")
        self.assertEqual(result["item_id"], "25858815414")
        self.assertFalse(result["exact_option_verified"])
        self.assertEqual(result["result"], "DIRECT_PRODUCT_ID_CANDIDATE")

    def test_intermediate_link_not_followed(self):
        hits = []
        def open_fn(req, timeout):
            hits.append(1)
            return FakeResponse(302, "https://link.coupang.com/a/SECOND")
        result = one_hop("https://link.coupang.com/a/FIRST", opener=open_fn)
        self.assertEqual(len(hits), 1)
        self.assertEqual(result["result"], "ADDITIONAL_HOP_NEEDED")

    def test_untrusted_redirect_rejected(self):
        def open_fn(req, timeout):
            return FakeResponse(302, "https://evil.example/track")
        result = one_hop("https://link.coupang.com/a/FIRST", opener=open_fn)
        self.assertEqual(result["result"], "NON_COUPANG_REDIRECT")
        self.assertIsNone(result["product_id"])

    def test_no_redirect_does_not_claim_product(self):
        result = one_hop("https://link.coupang.com/a/FIRST",
                         opener=lambda req, timeout: FakeResponse(403))
        self.assertIsNone(result["product_id"])
        self.assertEqual(result["result"], "NO_REDIRECT_TARGET")

    def test_external_url_not_requested(self):
        request_count = []
        def fn(req, timeout):
            request_count.append(1)
            raise AssertionError("should not fetch")
        with self.assertRaises(ValueError):
            one_hop("https://evil.example/a/123", opener=fn)
        self.assertEqual(request_count, [])


if __name__ == "__main__":
    unittest.main()
