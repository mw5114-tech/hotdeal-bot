"""Pure synthetic tests, no Tailscale or Coupang traffic."""
import unittest
from coupang_github_iphone_exit import validate_exit_ip, status_exit_ready, classify_report


class PhoneExitProbeTest(unittest.TestCase):
    def test_tailscale_ip(self):
        self.assertEqual(validate_exit_ip("100.100.12.34"), "100.100.12.34")
        for invalid in ("", "127.0.0.1", "192.168.0.2", "100.128.0.1", "example.com", "100.100.12.1;sh"):
            with self.subTest(ip=invalid), self.assertRaises(ValueError):
                validate_exit_ip(invalid)

    def test_online_exit_node(self):
        self.assertTrue(status_exit_ready({"ExitNodeStatus": {"ID": "node123", "Online": True}}))
        self.assertFalse(status_exit_ready({"ExitNodeStatus": {"ID": "node123", "Online": False}}))
        self.assertFalse(status_exit_ready({"ExitNodeStatus": None}))
        self.assertFalse(status_exit_ready({}))

    def test_classification(self):
        ok = {"browser_http": {
            "result": "BROWSER_RESPONSE", "main_status": 200,
            "final_host": "partners.coupang.com",
            "signals": {"deny_or_challenge_marker": False}
        }}
        self.assertEqual(classify_report(ok), "PARTNERS_HOME_REACHED_NOT_AUTHENTICATED")
        for data in (
            {**ok, "browser_http": {**ok["browser_http"], "main_status": 403}},
            {**ok, "browser_http": {**ok["browser_http"], "final_host": "example.com"}},
            {**ok, "browser_http": {**ok["browser_http"], "signals": {"deny_or_challenge_marker": True}}},
            {"browser_http": {"result": "BROWSER_SETUP_ERROR"}},
        ):
            self.assertEqual(classify_report(data), "PARTNERS_STILL_UNREACHABLE")


if __name__ == "__main__":
    unittest.main()
