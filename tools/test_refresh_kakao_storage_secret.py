"""Offline tests for local Kakao secret refresh; no network, login or GitHub write."""
import json
from types import SimpleNamespace
import unittest

from tools.refresh_kakao_storage_secret import upload_secret


class KakaoSecretRefreshTests(unittest.TestCase):
    def test_scopes_cookies_and_streams_via_stdin_not_shell(self):
        captured = {}
        state = {
            "cookies": [
                {"name": "kakao_login", "value": "secret-cookies-never-log",
                 "domain": ".kakao.com", "path": "/"},
                {"name": "other", "value": "foreign-cookie",
                 "domain": ".example.com", "path": "/"},
            ],
            "origins": [],
        }
        def fake_runner(args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            return SimpleNamespace(returncode=0)
        upload_secret(state, runner=fake_runner)
        self.assertEqual(captured["args"][:3], [
            "gh", "secret", "set",
        ])
        self.assertNotIn("secret-cookies-never-log", " ".join(captured["args"]))
        self.assertTrue(captured["kwargs"]["capture_output"])
        self.assertFalse(captured["kwargs"]["check"])
        saved = json.loads(captured["kwargs"]["input"])
        self.assertEqual([c["domain"] for c in saved["cookies"]], [".kakao.com"])

    def test_upload_failure_hides_stderr_even_if_it_contains_secret(self):
        def fake_runner(args, **kwargs):
            return SimpleNamespace(returncode=1, stderr="secret-cookies-never-log")
        with self.assertRaisesRegex(RuntimeError, "^GITHUB_CLI_SECRET_UPLOAD_FAILED$"):
            upload_secret({
                "cookies": [{"name": "sid", "value": "secret-cookies-never-log",
                             "domain": ".kakao.com", "path": "/"}],
                "origins": [],
            }, runner=fake_runner)

    def test_rejects_auth_state_with_no_kakao_cookies(self):
        def must_not_run(*args, **kwargs):
            self.fail("must never upload unscoped browser cookies")
        with self.assertRaisesRegex(ValueError, "NO_KAKAO_LOGIN_COOKIES"):
            upload_secret({
                "cookies": [{"name": "random", "value": "x",
                             "domain": "other.example", "path": "/"}],
                "origins": [],
            }, runner=must_not_run)


if __name__ == "__main__":
    unittest.main()
