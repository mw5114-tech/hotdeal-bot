"""Synthetic-only tests for GitHub runner home-router egress config."""
import base64
import unittest
from coupang_github_wg_route import render_config, check_endpoint, check_key

KEY = base64.b64encode(bytes(range(32))).decode()
ENV = {
    "COUPANG_WG_PRIVATE_KEY": KEY,
    "COUPANG_WG_PEER_PUBLIC_KEY": KEY,
    "COUPANG_WG_ADDRESS": "10.77.0.2/32",
    "COUPANG_WG_ENDPOINT": "home.example.net:51820",
}


class TestWireGuardConfig(unittest.TestCase):
    def test_synthetic_config(self):
        text = render_config(ENV)
        self.assertIn("Table = off", text)
        self.assertIn("AllowedIPs = 0.0.0.0/0", text)
        self.assertIn("Address = 10.77.0.2/32", text)
        self.assertIn("Endpoint = home.example.net:51820", text)

    def test_invalid_key_rejected(self):
        with self.assertRaises(ValueError):
            check_key("not-a-wireguard-key")

    def test_invalid_endpoint_rejected(self):
        for text in ("server:0", "server:65536", "server;command:51820", "server:1\nOther=1", ""):
            with self.subTest(text=text), self.assertRaises(ValueError):
                check_endpoint(text)

    def test_public_tunnel_address_rejected(self):
        with self.assertRaises(ValueError):
            render_config({**ENV, "COUPANG_WG_ADDRESS": "8.8.8.8/32"})


if __name__ == "__main__":
    unittest.main()
