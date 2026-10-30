"""Manual, read-only Naver Cafe access probe. Python 3.11 stdlib only.

Experimental web endpoint; schema must be verified against the live service.
No login automation, challenge solving, Telegram, state files, or retries.
"""
import json
import os
import re
from urllib import error, request

CAFE_ID = 24651941
LIST_URL = (
    "https://apis.naver.com/cafe-web/cafe2/ArticleListV2dot1.json"
    "?search.clubid=24651941&search.menuid=0&search.page=1"
    "&search.perPage=20&search.queryType=lastArticle"
)
REFERER = "https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L"
MAX_BYTES = 2_000_000


class ProbeError(Exception):
    """Only fixed, non-sensitive diagnostic codes may be passed here."""


def cookie_header(raw):
    # Only the two requested session cookies are sent to the fixed Naver host.
    if not raw or len(raw) > 16000 or any(ord(c) < 32 or ord(c) > 126 for c in raw):
        raise ProbeError("COOKIE_MISSING_OR_INVALID")
    pairs = {}
    for piece in raw.split(";"):
        name, sep, value = piece.strip().partition("=")
        if not sep or name not in {"NID_AUT", "NID_SES"} or name in pairs:
            raise ProbeError("COOKIE_FORMAT_INVALID")
        if not value or re.search(r'[\s"\\,;]', value):
            raise ProbeError("COOKIE_FORMAT_INVALID")
        pairs[name] = value
    if set(pairs) != {"NID_AUT", "NID_SES"}:
        raise ProbeError("COOKIE_PAIR_REQUIRED")
    return "; ".join(name + "=" + pairs[name] for name in ("NID_AUT", "NID_SES"))


def mask_secrets(raw):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    # Mask components as well as the composite GitHub Secret, before any I/O.
    for value in [raw] + [p.partition("=")[2] for p in raw.split(";")]:
        if value:
            safe = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::add-mask::" + safe)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward Cookie to login, challenge, or another host/path.
        raise ProbeError("REDIRECT_STOPPED")


def fetch(cookie=None):
    headers = {"User-Agent": "NaverCafeAccessProbe/1.0 (owner permitted)",
               "Accept": "application/json", "Referer": REFERER}
    if cookie:
        headers["Cookie"] = cookie
    req = request.Request(LIST_URL, headers=headers)
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=25) as resp:
            data = resp.read(MAX_BYTES + 1)
    except ProbeError:
        raise
    except error.HTTPError as exc:
        code = {401: "HTTP_AUTH_REQUIRED", 403: "HTTP_FORBIDDEN",
                429: "HTTP_RATE_LIMITED"}.get(exc.code, "HTTP_ERROR")
        raise ProbeError(code) from None
    except (error.URLError, OSError):
        raise ProbeError("NETWORK_OR_DNS_ERROR") from None
    if len(data) > MAX_BYTES:
        raise ProbeError("RESPONSE_TOO_LARGE")
    return data


def parse_listing(data):
    # Inspect errors only for classification. Never log the body or API message.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ProbeError("RESPONSE_ENCODING_UNSUPPORTED") from None
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        lower = text.lower()
        if any(s in lower for s in ("captcha", "자동입력", "보안문자", "추가 인증", "추가인증")):
            raise ProbeError("CHALLENGE_STOPPED") from None
        if any(s in lower for s in ("nid.naver.com", "로그인")):
            raise ProbeError("LOGIN_REQUIRED") from None
        raise ProbeError("NON_JSON_OR_SCHEMA_CHANGED") from None
    message = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(message, dict):
        raise ProbeError("SCHEMA_CHANGED")
    status = message.get("status")
    if status not in ("200", 200):
        # Restrict keyword search to error envelopes, never article titles.
        lower = json.dumps(message, ensure_ascii=False).lower()
        if any(s in lower for s in ("captcha", "자동입력", "보안문자", "추가인증", "추가 인증")):
            raise ProbeError("CHALLENGE_STOPPED")
        if any(s in lower for s in ("로그인", "login", "인증")):
            raise ProbeError("LOGIN_REQUIRED")
        if any(s in lower for s in ("멤버", "회원", "등급", "권한", "member", "permission")):
            raise ProbeError("MEMBERSHIP_OR_PERMISSION_REQUIRED")
        # Only expose a three-digit status code; never output Naver's response body.
        if isinstance(status, (int, str)) and re.fullmatch(r"[1-5][0-9]{2}", str(status)):
            raise ProbeError("API_STATUS_" + str(status))
        raise ProbeError("API_REJECTED_OR_SCHEMA_CHANGED")
    result = message.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("articleList"), list):
        raise ProbeError("SCHEMA_CHANGED")
    articles = {}
    for row in result["articleList"]:
        if not isinstance(row, dict):
            raise ProbeError("ARTICLE_SCHEMA_CHANGED")
        article_id, title = row.get("articleId"), row.get("subject")
        if type(article_id) is not int or article_id <= 0 or not isinstance(title, str) or not title.strip():
            raise ProbeError("ARTICLE_SCHEMA_CHANGED")
        articles[article_id] = title
    if not articles:
        raise ProbeError("EMPTY_LIST_INCONCLUSIVE")
    return articles


def run(fetcher=fetch):
    raw = os.environ.get("NAVER_COOKIE", "")
    mask_secrets(raw)
    try:
        cookie = cookie_header(raw)
        authenticated = parse_listing(fetcher(cookie))
    except ProbeError as exc:
        print("FAIL:", str(exc))
        return 2
    # A readable public list is not evidence that the cookie authenticated.
    try:
        anonymous = parse_listing(fetcher())
    except ProbeError as exc:
        if str(exc) in {"LOGIN_REQUIRED", "HTTP_AUTH_REQUIRED", "MEMBERSHIP_OR_PERMISSION_REQUIRED"}:
            print(f"PASS: AUTHENTICATED_LIST_READABLE cafe={CAFE_ID} menu=0 page=1 articles={len(authenticated)}")
            print("Scope: first page only; board coverage and notice filtering not yet verified.")
            return 0
        print("INCONCLUSIVE: AUTH_LIST_READABLE_CONTROL_FAILED reason=" + str(exc))
        return 3
    # Differences may be caused by new posts or personalization; not proof.
    print(f"INCONCLUSIVE: LIST_READABLE_WITHOUT_AUTH authenticated_count={len(authenticated)} anonymous_count={len(anonymous)}")
    return 3


def main():
    try:
        return run()
    except Exception:
        # No traceback: third-party errors may contain headers or response data.
        print("FAIL: UNEXPECTED_INTERNAL_ERROR (details suppressed)")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

