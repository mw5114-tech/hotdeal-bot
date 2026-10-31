"""One-shot public network diagnosis in GitHub ubuntu-slim.

Checks AAAA/IPv6 routing and makes at most one GET to the official homepage.
Never signs in, collects cookies, spoofs an IP, uses a proxy, or retries on denial.
"""
from __future__ import annotations
import json
import os
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

HOST = "partners.coupang.com"
TARGET = "https://" + HOST + "/"
DENIED = re.compile(
    r"access\s*denied|forbidden|request\s*blocked|"
    r"captcha|verify\s+you\s+are\s+human|접근[이\s]*차단", re.I
)
BRAND = re.compile(r"쿠팡\s*파트너스|coupang\s*partners", re.I)


def dns_summary():
    result = {"ipv4_records": 0, "ipv6_records": 0, "dns_ok": False}
    try:
        records = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
        result["ipv4_records"] = len(set(x[4][0] for x in records if x[0] == socket.AF_INET))
        result["ipv6_records"] = len(set(x[4][0] for x in records if x[0] == socket.AF_INET6))
        result["dns_ok"] = True
    except OSError as exc:
        result["error_type"] = type(exc).__name__
    return result


def ipv6_route():
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.connect(("2606:4700:4700::1111", 53))
        addr = sock.getsockname()[0]
        sock.close()
        return {"available": bool(addr and addr != "::")}
    except OSError as exc:
        return {"available": False, "error_type": type(exc).__name__}


def one_page():
    req = urllib.request.Request(TARGET, method="GET")
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    except (OSError, urllib.error.URLError) as exc:
        return {"result": "NETWORK_ERROR", "error_type": type(exc).__name__}
    with response:
        status = response.status if hasattr(response, "status") else response.code
        body = response.read(16_384).decode("utf-8", errors="replace")
        final_host = urlsplit(response.url).hostname
        denied = bool(DENIED.search(body))
        brand = bool(BRAND.search(body))
        return {
            "result": (
                "PUBLIC_SITE_TEXT_CONFIRMED"
                if status == 200 and final_host == HOST and brand and not denied
                else "DENIED_OR_UNCONFIRMED"
            ),
            "status": status,
            "final_host": final_host,
            "denied_marker": denied,
            "brand_marker": brand,
            "edge_server": response.headers.get("Server", "")[:60],
        }


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("GITHUB_ACTIONS_REQUIRED")
        return 2
    data = {
        "probe": "UBUNTU_SLIM_NETWORK_ONESHOT",
        "runner_os": os.environ.get("RUNNER_OS", ""),
        "dns": dns_summary(),
        "ipv6_route": ipv6_route(),
        "public_page": one_page(),
        "login_attempted": False,
        "max_site_requests": 1,
    }
    print(json.dumps(data, indent=2))
    with open("coupang-slim-diagnostic.json", "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
