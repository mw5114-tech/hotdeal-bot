"""Offline-only checks for the opt-in Chrome helper extension.

The only live browser is user's own manually opened, authenticated Chrome.
Never import cookies, inspect login fields, use private backend endpoints,
auto-run without clicks, or forward product URLs/affiliate links elsewhere.
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent


class ExtensionContractTests(unittest.TestCase):
    def test_manifest_minimal_and_no_auth_page(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(set(manifest["host_permissions"]), {"https://partners.coupang.com/*"})
        self.assertEqual(set(manifest["permissions"]), {"tabs", "scripting", "storage"})
        self.assertEqual(manifest["action"]["default_popup"], "popup.html")

    def test_code_does_not_read_cookies_or_use_unsupported_backend(self):
        src = (ROOT / "popup.js").read_text(encoding="utf-8")
        for prohibited in ("chrome.cookies", "document.cookie", "fetch(",
                           "XMLHttpRequest", "webRequest", "webNavigation",
                           "debugger", "eval(", "new Function("):
            self.assertNotIn(prohibited, src)
        self.assertIn('generateInPage', src)
        self.assertIn('inputInPage', src)
        self.assertIn('readGeneratedLinkInPage', src)
        self.assertIn('canonicalProductURL', src)

    def test_buttons_all_wired_to_user_clicks(self):
        markup = (ROOT / "popup.html").read_text(encoding="utf-8")
        src = (ROOT / "popup.js").read_text(encoding="utf-8")
        for name in ("open", "fill", "generate", "extract", "copy"):
            self.assertIn(f'id="{name}"', markup)
        self.assertIn('addEventListener("click"', src)
        self.assertNotIn("setInterval(", src)

    def test_no_permission_to_coupang_login_or_product_site(self):
        manifest = (ROOT / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("login.coupang.com", manifest)
        self.assertNotIn("www.coupang.com/*", manifest)


if __name__ == "__main__":
    unittest.main()
