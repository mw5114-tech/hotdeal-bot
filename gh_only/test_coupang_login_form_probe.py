"""Offline classification tests; never contact Coupang."""
import unittest

from gh_only.coupang_login_form_probe import classify, safe_location


class PublicLoginFormProbeTests(unittest.TestCase):
    def test_visible_login_form(self):
        self.assertEqual(classify(
            page_url="https://login.coupang.com/login/login.pang?rtnUrl=example",
            status=200, visible_text="로그인", password_visible=True,
            login_clicked=True, login_button_found=True,
        ), "LOGIN_FORM_VISIBLE")

    def test_access_denied(self):
        self.assertEqual(classify(
            page_url="https://login.coupang.com/login/login.pang",
            status=403, visible_text="사용권한이 없습니다",
            password_visible=False, login_clicked=True, login_button_found=True,
        ), "ACCESS_DENIED_OR_CHALLENGE")

    def test_login_click_not_available(self):
        self.assertEqual(classify(
            page_url="https://partners.coupang.com/",
            status=200, visible_text="공개 메인", password_visible=False,
            login_clicked=False, login_button_found=False,
        ), "LOGIN_BUTTON_NOT_FOUND")

    def test_login_page_without_form(self):
        self.assertEqual(classify(
            page_url="https://login.coupang.com/login/login.pang",
            status=200, visible_text="로그인", password_visible=False,
            login_clicked=True, login_button_found=True,
        ), "LOGIN_PAGE_NO_VISIBLE_PASSWORD_FIELD")

    def test_query_never_logged(self):
        self.assertEqual(safe_location(
            "https://login.coupang.com/login/login.pang?rtnUrl=SECRET#secret"
        ), {"host": "login.coupang.com", "path": "/login/login.pang"})


if __name__ == "__main__":
    unittest.main()
