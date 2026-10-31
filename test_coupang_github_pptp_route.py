"""Offline-only unit tests. Does not contact router or Coupang."""
import unittest
from coupang_github_pptp_route import make_peer_config, validate_credentials, validate_endpoint

ENV = {
    "COUPANG_PPTP_ENDPOINT": "home.iptime.org",
    "COUPANG_PPTP_USER": "githubcoupang",
    "COUPANG_PPTP_PASSWORD": "ExampleOnly123!xyz",
}

class TestPPTP(unittest.TestCase):
    def test_profile(self):
        profile = make_peer_config(ENV)
        self.assertIn('pty "pptp home.iptime.org --nolaunchpppd"', profile)
        self.assertIn('name "githubcoupang"', profile)
        self.assertIn("nodefaultroute", profile)
        self.assertIn("require-mppe-128", profile)
        self.assertIn("refuse-pap", profile)

    def test_host_restrictions(self):
        for host in ("http://example.com", "127.0.0.1:1723", "bad host", "foo;rm -rf /"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_endpoint(host)

    def test_safe_credential_syntax(self):
        with self.assertRaises(ValueError):
            validate_credentials("user", 'a"b'*8)
        with self.assertRaises(ValueError):
            validate_credentials("user;ls", "StrongPassword123!")
        with self.assertRaises(ValueError):
            validate_credentials("user", "123")

if __name__ == "__main__":
    unittest.main()
