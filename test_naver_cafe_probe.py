import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
import naver_cafe_probe as app


def listing(rows=None):
    if rows is None:
        rows = [{"articleId": 112028, "subject": "private fixture title"}]
    return json.dumps({"message": {"status": "200", "result": {"articleList": rows}}}).encode()


COOKIE = "NID_AUT=fake-aut; NID_SES=fake-ses"


class Tests(unittest.TestCase):
    def test_cookie_pair_and_order(self):
        self.assertEqual(app.cookie_header("NID_SES=fake-ses; NID_AUT=fake-aut"), COOKIE)

    def test_invalid_cookies(self):
        for raw in ("", "NID_AUT=a", COOKIE + "\r\nX: y", COOKIE + "; extra=x",
                    COOKIE + "; NID_AUT=duplicate", "NID_AUT=; NID_SES=b", "Cookie: " + COOKIE,
                    "NID_AUT=a b; NID_SES=c"):
            with self.subTest(raw=raw), self.assertRaises(app.ProbeError):
                app.cookie_header(raw)

    def test_listing_and_separate_notices(self):
        obj = json.loads(listing())
        obj["message"]["result"]["noticeList"] = [{"articleId": 1, "subject": "notice"}]
        self.assertEqual(app.parse_listing(json.dumps(obj).encode()), {112028: "private fixture title"})

    def test_current_boardlist_api_nested_rows(self):
        data = {
            "result": {"articleList": [
                {"type": "NOTICE", "item": {"articleId": 11, "subject": "Pinned notice"}},
                {"type": "ARTICLE", "item": {"articleId": "112028", "subject": "New hot deal"}},
                {"type": "ARTICLE", "item": {"articleId": 112029, "subject": "Next deal"}}
            ]}
        }
        self.assertEqual(app.parse_listing(json.dumps(data).encode()),
                         {112028: "New hot deal", 112029: "Next deal"})
        data = {"articles": [{"articleId": 112028, "title": "New hot deal"}]}
        self.assertEqual(app.parse_listing(json.dumps(data).encode()), {112028: "New hot deal"})

    def test_current_boardlist_error_envelope(self):
        data = {"error": {"errorCode": "LOGIN_REQUIRED", "reason": "로그인 필요"}}
        with self.assertRaises(app.ProbeError) as ctx:
            app.parse_listing(json.dumps(data).encode())
        self.assertEqual(str(ctx.exception), "LOGIN_REQUIRED")

    def test_notice_like_title_is_not_a_challenge(self):
        self.assertEqual(len(app.parse_listing(listing([{"articleId": 2, "subject": "로그인 CAPTCHA 문의"}]))), 1)

    def test_fail_closed_responses(self):
        cases = [(b'<html>captcha</html>', "CHALLENGE_STOPPED"),
                 ('<html>로그인</html>'.encode(), "LOGIN_REQUIRED"),
                 (b'<html>app shell</html>', "NON_JSON_OR_SCHEMA_CHANGED"),
                 (b'{}', "SCHEMA_CHANGED"),
                 (listing([]), "EMPTY_LIST_INCONCLUSIVE"),
                 (listing([{"articleId": True, "subject": "bad"}]), "ARTICLE_SCHEMA_CHANGED"),
                 (listing([{"articleId": 1}]), "ARTICLE_SCHEMA_CHANGED")]
        for body, code in cases:
            with self.subTest(code=code), self.assertRaises(app.ProbeError) as ctx:
                app.parse_listing(body)
            self.assertEqual(str(ctx.exception), code)

    def test_api_error_classification(self):
        for message, code in [("로그인 필요", "LOGIN_REQUIRED"),
                              ("카페 회원 권한 필요", "MEMBERSHIP_OR_PERMISSION_REQUIRED"),
                              ("추가 인증 captcha", "CHALLENGE_STOPPED"),
                              ("unknown", "API_STATUS_403")]:
            with self.subTest(code=code), self.assertRaises(app.ProbeError) as ctx:
                app.parse_listing(json.dumps({"message": {"status": "403", "error": message}}).encode())
            self.assertEqual(str(ctx.exception), code)

    def test_safe_numeric_status_diagnostics(self):
        accepted = json.loads(listing())
        accepted["message"]["status"] = 200
        self.assertEqual(app.parse_listing(json.dumps(accepted).encode()), {112028: "private fixture title"})
        for status in ("400", "500", 403):
            payload = json.dumps({"message": {"status": status, "error": "unknown"}}).encode()
            with self.assertRaises(app.ProbeError) as ctx:
                app.parse_listing(payload)
            self.assertEqual(str(ctx.exception), "API_STATUS_" + str(status))

    def run_probe(self, fetcher, cookie=COOKIE):
        output = io.StringIO()
        with patch.dict(os.environ, {"NAVER_COOKIE": cookie, "GITHUB_ACTIONS": "false"}), contextlib.redirect_stdout(output):
            code = app.run(fetcher)
        self.assertNotIn("fake-aut", output.getvalue())
        self.assertNotIn("private fixture title", output.getvalue())
        return code, output.getvalue()

    def test_authenticated_access_requires_negative_control(self):
        calls = []
        def fetch(cookie=None):
            calls.append(cookie)
            if cookie is None:
                raise app.ProbeError("LOGIN_REQUIRED")
            return listing()
        code, output = self.run_probe(fetch)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [COOKIE, None])
        self.assertIn("AUTHENTICATED_LIST_READABLE", output)

    def test_public_list_does_not_prove_auth(self):
        code, output = self.run_probe(lambda cookie=None: listing())
        self.assertEqual(code, 3)
        self.assertIn("LIST_READABLE_WITHOUT_AUTH", output)

    def test_challenge_stops_without_control_or_retry(self):
        calls = []
        def fetch(cookie=None):
            calls.append(cookie)
            raise app.ProbeError("CHALLENGE_STOPPED")
        self.assertEqual(self.run_probe(fetch)[0], 2)
        self.assertEqual(len(calls), 1)

    def test_control_network_failure_inconclusive(self):
        def fetch(cookie=None):
            if cookie is None:
                raise app.ProbeError("NETWORK_OR_DNS_ERROR")
            return listing()
        self.assertEqual(self.run_probe(fetch)[0], 3)

    def test_missing_cookie_does_not_connect(self):
        self.assertEqual(self.run_probe(lambda *_: self.fail("network called"), cookie="")[0], 2)

    def test_redirect_never_forwards_cookie(self):
        with self.assertRaises(app.ProbeError) as ctx:
            app.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com/")
        self.assertEqual(str(ctx.exception), "REDIRECT_STOPPED")

    def test_network_request_fixed_destination_and_bounded_read(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                self.size = size
                return listing()
        response = Response()
        with patch.object(app.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            app.fetch(COOKIE)
            req = opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url, app.LIST_URL)
            self.assertEqual(req.get_header("Cookie"), COOKIE)
            self.assertEqual(response.size, app.MAX_BYTES + 1)

    def test_oversize_and_network_errors(self):
        with patch.object(app.request, "build_opener") as opener:
            opener.return_value.open.side_effect = app.error.URLError("sensitive text")
            with self.assertRaisesRegex(app.ProbeError, "^NETWORK_OR_DNS_ERROR$"):
                app.fetch(COOKIE)

    def test_unexpected_exception_never_logged(self):
        out = io.StringIO()
        with patch.object(app, "run", side_effect=RuntimeError(COOKIE)), contextlib.redirect_stdout(out):
            self.assertEqual(app.main(), 2)
        self.assertNotIn(COOKIE, out.getvalue())


if __name__ == "__main__":
    unittest.main()

