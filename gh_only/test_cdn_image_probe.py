"""Proof-of-transport image CDN tests; no HTTP in unit tests."""
import io
import unittest
from gh_only.cdn_image_probe import is_cdn_image_url, image_signature, fetch_one
from pathlib import Path
import tempfile

JPEG=b"\xff\xd8\xff\xdb"+b"\x00"*20

class FakeResponse:
    def __init__(self,data):
        self.data=data
        self.status=200
        self.headers={"Content-Type":"image/jpeg"}
    def geturl(self):
        return "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/test.jpg"
    def read(self,n):
        return self.data
    def __enter__(self): return self
    def __exit__(self,*args): pass

class CDNTests(unittest.TestCase):
    def test_valid_host(self):
        self.assertTrue(is_cdn_image_url(
            "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/test.jpg"))
        self.assertFalse(is_cdn_image_url("https://thumbnail.coupangcdn.com.evil.test/a.jpg"))
        self.assertFalse(is_cdn_image_url("http://thumbnail.coupangcdn.com/a.jpg"))

    def test_jpeg_magic(self):
        self.assertEqual(image_signature(JPEG),"jpeg")
        self.assertIsNone(image_signature(b"<html>access denied</html>"))

    def test_proof_is_not_item_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"candidate.jpg"
            report=fetch_one(
                "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/test.jpg",
                p,opener=lambda req,timeout:FakeResponse(JPEG))
            self.assertTrue(report["image_saved"])
            self.assertTrue(p.exists())
            self.assertFalse(report["exact_product_image_verified"])
            self.assertFalse(report["rights_to_republish_verified"])

    def test_no_save_for_access_denied_html(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"candidate.jpg"
            response=FakeResponse(b"<html>Access Denied</html>")
            response.headers={"Content-Type":"text/html"}
            report=fetch_one(
                "https://thumbnail.coupangcdn.com/thumbnails/remote/492x492ex/image/test.jpg",
                p,opener=lambda req,timeout:response)
            self.assertFalse(report["image_saved"])
            self.assertFalse(p.exists())

if __name__=="__main__":
    unittest.main()
