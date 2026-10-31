"""One-shot PPTP connection setup for ipTIME N704SR on GitHub-hosted Linux.

Configure the user's own router first. The VPN profile is created only at runtime,
stored as a root-owned file, and deleted at the end of the workflow.
No credentials, profile, external IP, or router address are logged.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

HOST = "partners.coupang.com"
PEER_NAME = "coupang-pptp"
PEER_PATH = Path("/etc/ppp/peers") / PEER_NAME

def validate_endpoint(value: str) -> str:
    # Domain or IPv4; PPTP runs over TCP 1723 and GRE protocol 47.
    if not isinstance(value, str) or not 1 <= len(value) <= 253:
        raise ValueError("PPTP_ENDPOINT_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", value):
        raise ValueError("PPTP_ENDPOINT_INVALID")
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        if not re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value
        ) or "." not in value:
            raise ValueError("PPTP_ENDPOINT_INVALID")
    return value

def validate_credentials(username: str, password: str) -> tuple[str, str]:
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
        raise ValueError("PPTP_USER_INVALID")
    # Restrict new, dedicated router VPN password to chars parseable in pppd
    # option files without shell or pppd quote escapes.
    if not isinstance(password, str) or not re.fullmatch(r"[A-Za-z0-9_!@#%+=:.,-]{12,100}", password):
        raise ValueError("PPTP_PASSWORD_INVALID")
    return username, password

def make_peer_config(env: dict[str, str]) -> str:
    endpoint = validate_endpoint(env.get("COUPANG_PPTP_ENDPOINT", ""))
    user, password = validate_credentials(
        env.get("COUPANG_PPTP_USER", ""), env.get("COUPANG_PPTP_PASSWORD", "")
    )
    # The user's PPTP server should be configured for MPPE on the router.
    # Username and password will be deleted after the run.
    return (
        f'pty "pptp {endpoint} --nolaunchpppd"\n'
        f'name "{user}"\n'
        f'password "{password}"\n'
        "remotename PPTP\n"
        "noauth\n"
        "require-mppe-128\n"
        "refuse-eap\n"
        "refuse-pap\n"
        "refuse-chap\n"
        "refuse-mschap\n"
        "nodefaultroute\n"
        "noipdefault\n"
        "nobsdcomp\n"
        "nodeflate\n"
        "lcp-echo-interval 10\n"
        "lcp-echo-failure 3\n"
        "maxfail 1\n"
        "unit 0\n"
    )

def write_profile(env: dict[str, str]) -> None:
    if env.get("GITHUB_ACTIONS") != "true" or env.get("RUNNER_OS") != "Linux":
        raise ValueError("GITHUB_LINUX_REQUIRED")
    text = make_peer_config(env)
    PEER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PEER_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(text)
    print("PPTP_PROFILE_READY")

def get_targets() -> list[str]:
    found: set[str] = set()
    for ai in socket.getaddrinfo(HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM):
        found.add(str(ipaddress.IPv4Address(ai[4][0])))
    if not found or len(found) > 16:
        raise ValueError("PARTNERS_DNS_UNEXPECTED")
    return sorted(found)

def add_partner_routes() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise ValueError("GITHUB_LINUX_REQUIRED")
    subprocess.run(["ip", "link", "show", "ppp0"], check=True, capture_output=True)
    addresses = get_targets()
    for ip in addresses:
        subprocess.run(["sudo", "ip", "route", "replace", f"{ip}/32", "dev", "ppp0"], check=True)
    print("PARTNERS_ONLY_ROUTES_INSTALLED", len(addresses))

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("profile", "route"))
    args = p.parse_args()
    try:
        if args.action == "profile":
            write_profile(os.environ)
        else:
            add_partner_routes()
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch("[A-Z_]+", str(exc)) else "PPTP_SETUP_FAILED"
        print(code, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
