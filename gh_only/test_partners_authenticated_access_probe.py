"""Offline auth state filtering tests. No real credentials used."""
import json
import unittest

from gh_only.partners_authenticated_access_probe import safe_coupang_state, is_coupang_domain


class AuthStateTests(unittest.TestCase):
    def test_safe_coupang_subdomains(self):
        for host in ("coupang.com",".coupang.com","partners.coupang.com",
                     ".login.coupang.com","www.coupang.com"):
            self.assertTrue(is_coupang_domain(host))

    def test_lookalike_host_not_imported(self):
        for host in ("fakecoupang.com","coupang.com.attacker.test",
                     "coupang.net",None,".evil.example"):
            self.assertFalse(is_coupang_domain(host))

    def test_strips_unrelated_cookies_and_origins(self):
        state={"cookies":[{"name":"placeholder", "value":"FAKE",
                            "domain":".coupang.com", "path":"/"},
                          {"name":"bad","value":"FAKE","domain":"evil.example","path":"/"}],
               "origins":[{"origin":"https://partners.coupang.com", "localStorage":[]},
                          {"origin":"https://evil.example", "localStorage":[]}]}
        scoped=safe_coupang_state(json.dumps(state))
        self.assertEqual(len(scoped["cookies"]),1)
        self.assertEqual(len(scoped["origins"]),1)

    def test_no_coupang_cookie_fails(self):
        with self.assertRaisesRegex(ValueError,"NO_COUPANG_SESSION_COOKIES"):
            safe_coupang_state(json.dumps({"cookies":[{"domain":"example.com"}],"origins":[]}))

    def test_bad_json_fails(self):
        with self.assertRaises(ValueError):
            safe_coupang_state("not json")


if __name__ == "__main__":
    unittest.main()
