"""Offline checks for the read-only Naver Blog diagnostic."""
import unittest

from naver_blog_probe import editor_state, safe_site, validate_blog_id


class NaverBlogProbeTests(unittest.TestCase):
    def test_own_blog_id(self):
        self.assertEqual(validate_blog_id("mw5114"), "mw5114")

    def test_rejects_url_instead_of_blog_id(self):
        with self.assertRaises(ValueError):
            validate_blog_id("https://blog.naver.com/mw5114")

    def test_login_redirect_is_not_mistaken_for_editor(self):
        self.assertEqual(
            editor_state([
                "https://blog.naver.com/PostWriteForm.naver?blogId=mw5114",
                "https://nid.naver.com/nidlogin.login?mode=form",
            ]),
            "LOGIN_REQUIRED",
        )

    def test_editor_frame(self):
        self.assertEqual(
            editor_state(["https://blog.naver.com/PostWriteForm.naver?blogId=mw5114"]),
            "EDITOR_FRAME_PRESENT",
        )

    def test_unknown_is_not_success(self):
        self.assertEqual(editor_state(["https://blog.naver.com/mw5114"]), "EDITOR_UNCONFIRMED")

    def test_safe_site_hides_sensitive_query_string(self):
        self.assertEqual(safe_site("https://nid.naver.com/login?token=NEVER-LOG"), "nid.naver.com")


if __name__ == "__main__":
    unittest.main()
