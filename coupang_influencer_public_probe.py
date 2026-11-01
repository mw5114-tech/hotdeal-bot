"""One request to Coupang's publicly documented alternate creator portal.

This does not log in, use cookies, create a partner URL, or probe private APIs.
Coupang Influencers is a separate official product and may not be compatible
with a Coupang Partners account.
"""
from __future__ import annotations
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://influencers.coupang.com/"


def main():
    req = Request(URL, headers={"Accept": "text/html"}, method="GET")
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as error:
        response = error
    except (URLError, OSError) as error:
        print(json.dumps({"portal": "Coupang Influencers", "network_error": type(error).__name__,
                          "new_affiliate_url_issued": False}))
        return 0
    with response:
        body = response.read(18000).decode("utf-8", errors="replace")
        status = response.code
        result = {
            "portal": "Coupang Influencers",
            "status": status,
            "response_server": response.headers.get("Server", "")[:70],
            "access_denied_marker": "access denied" in body.lower(),
            "influencers_text_marker": ("인플루언서" in body or "influencers" in body.lower()),
            "new_affiliate_url_issued": False,
            "account_access_tested": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    main()
