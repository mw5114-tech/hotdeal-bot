"""Offline configuration tests only; never contacts external sites."""
import unittest
from coupang_independent_proxy_probe import proxy_config, read_ip


class IndependentProxyTests(unittest.TestCase):
    def test_proxy_without_credentials(self):
        self.assertEqual(proxy_config("socks5://gateway.example.org:1080"),
                         {"server": "socks5://gateway.example.org:1080"})

    def test_http_credentials_are_separate(self):
        self.assertEqual(proxy_config("http://proxy.example.org:3128", "alice", "secret"),
                         {"server": "http://proxy.example.org:3128",
                          "username": "alice", "password": "secret"})

    def test_invalid_server_is_rejected(self):
        for candidate in (
            "", "ftp://host:21", "https://host", "https://user:password@host:443",
            "https://host:443/private", "https://host:443?token=secret",
            "https://host:999999", "http://host:1000\r\nx:1",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    proxy_config(candidate)

    def test_partial_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            proxy_config("http://proxy.example.org:8080", "alice", "")

    def test_ip_parser(self):
        self.assertEqual(read_ip("203.0.113.5\n"), "203.0.113.5")
        self.assertEqual(read_ip("2001:db8::1"), "2001:db8::1")
        self.assertIsNone(read_ip("this is not an IP"))


if __name__ == "__main__":
    unittest.main()
