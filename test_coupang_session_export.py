"""Offline synthetic tests only: no Coupang requests or real credentials."""
import base64
import json
import unittest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from coupang_session_export import (
    SCHEMA, make_payload, normalize_cookies, encrypt_payload, validate_member_url,
)

DUMMY = {"name": "DUMMY", "value": "synthetic", "domain": ".partners.coupang.com",
         "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}


class ExportTest(unittest.TestCase):
    def test_member_url(self):
        expected = "https://partners.coupang.com/#affiliate/ws/link-to-any-page"
        self.assertEqual(validate_member_url(expected), expected)
        for v in ("https://evil.com/", "https://partners.coupang.com/",
                  "https://partners.coupang.com/login", "https://partners.coupang.com/?t=1"):
            with self.subTest(v=v), self.assertRaises(ValueError):
                validate_member_url(v)

    def test_only_coupang_cookies_exported(self):
        other = {**DUMMY, "domain": ".google.com", "value": "DO_NOT_EXPORT"}
        cookies = normalize_cookies([other, DUMMY])
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["value"], "synthetic")

    def test_partitioned_cookie_not_transferred(self):
        with self.assertRaisesRegex(ValueError, "PARTITIONED"):
            normalize_cookies([{**DUMMY, "partitionKey": "https://x.com"}])

    def test_envelope_matches_existing_actions_schema(self):
        state = make_payload("https://partners.coupang.com/#affiliate/ws/link-to-any-page",
                             [DUMMY], {"test": "dummy"})
        envelope, key_text = encrypt_payload(state)
        blob = json.loads(envelope)
        restored = json.loads(AESGCM(base64.b64decode(key_text)).decrypt(
            base64.b64decode(blob["iv"]), base64.b64decode(blob["ciphertext"]), SCHEMA.encode()))
        self.assertEqual(restored, state)
        self.assertEqual(restored["state"]["origins"][0]["origin"], "https://partners.coupang.com")
        self.assertEqual(len(base64.b64decode(key_text)), 32)


if __name__ == "__main__":
    unittest.main()
