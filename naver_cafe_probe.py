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
    "https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/"
    "24651941/menus/0/articles?page=1&pageSize=50&sortBy=TIME&viewType=L"
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
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
               "Accept": "application/json, text/plain, */*", "Referer": REFERER}
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
    if not isinstance(obj, dict):
        raise ProbeError("SCHEMA_CHANGED")
    # New boardlist API: result.articleList, or result.articles with nested item.
    # Old API: message.status + message.result.articleList.
    message = obj.get("message")
    if isinstance(message, dict) and "status" in message:
        status = message["status"]
        if status not in ("200", 200):
            lower = json.dumps(message, ensure_ascii=False).lower()
            if any(x in lower for x in ("captcha", "자동입력", "보안문자", "추가 인증", "추가인증")):
                raise ProbeError("CHALLENGE_STOPPED")
            if any(x in lower for x in ("로그인", "login", "인증")):
                raise ProbeError("LOGIN_REQUIRED")
            if any(x in lower for x in ("멤버", "회원", "등급", "권한", "member", "permission")):
                raise ProbeError("MEMBERSHIP_OR_PERMISSION_REQUIRED")
            if isinstance(status, (int, str)) and re.fullmatch(r"[1-5][0-9]{2}", str(status)):
                raise ProbeError("API_STATUS_" + str(status))
            raise ProbeError("API_REJECTED_OR_SCHEMA_CHANGED")
    api_error = obj.get("error")
    if isinstance(api_error, dict):
        lower = json.dumps(api_error, ensure_ascii=False).lower()
        if any(x in lower for x in ("captcha", "자동입력", "보안문자", "추가 인증", "추가인증")):
            raise ProbeError("CHALLENGE_STOPPED")
        if "adultauth" in lower or "성인인증" in lower:
            raise ProbeError("ADULT_AUTH_REQUIRED")
        if any(x in lower for x in ("로그인", "login", "auth", "member", "permission", "권한", "가입")):
            raise ProbeError("LOGIN_REQUIRED")
        raise ProbeError("API_ERROR_ENVELOPE")
    if isinstance(message, dict) and isinstance(message.get("result"), dict):
        result = message["result"]
    elif isinstance(obj.get("result"), dict):
        result = obj["result"]
    else:
        result = obj
    raw_articles = result.get("articleList")
    if raw_articles is None:
        raw_articles = result.get("articles")
    if not isinstance(raw_articles, list):
        raise ProbeError("SCHEMA_CHANGED")
    articles = {}
    for row in raw_articles:
        if not isinstance(row, dict):
            raise ProbeError("ARTICLE_SCHEMA_CHANGED")
        if row.get("type") in ("NOTICE", "AD", "ADVERTISEMENT"):
            continue
        node = row.get("item") if isinstance(row.get("item"), dict) else row
        article_id = node.get("articleId", node.get("articleid"))
        if isinstance(article_id, str) and article_id.isdigit():
            article_id = int(article_id)
        title = node.get("subject") or node.get("title")
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
    # Only metadata (article ids and titles) was checked. Session authentication
    # remains unverified when the anonymous request can read the same listing.
    if authenticated == anonymous:
        print(f"PASS: ARTICLE_METADATA_READABLE_WITHOUT_AUTH articles={len(authenticated)}")
        print("AUTH_NOT_VERIFIED: public listing metadata does not prove access to member-only articles.")
        return 0
    # Different lists could result from login or concurrent new posts.
    print(f"INCONCLUSIVE: AUTH_AND_ANON_LISTS_DIFFER authenticated_count={len(authenticated)} anonymous_count={len(anonymous)}")
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

