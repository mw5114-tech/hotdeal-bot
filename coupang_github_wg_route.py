"""Prepare a route-scoped WireGuard tunnel for GitHub Actions (Linux only).

The home router, not the user's PC, must host a reachable WireGuard VPN server.
This never sends secrets to logs or to any third-party service. The only routed
destination in this experiment is the resolved Coupang Partners IPv4 address.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

HOST = "partners.coupang.com"


def check_key(raw: str) -> str:
    value = raw.strip()
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("INVALID_WIREGUARD_KEY") from exc
    if len(decoded) != 32:
        raise ValueError("INVALID_WIREGUARD_KEY")
    return value


def check_endpoint(raw: str) -> str:
    # Literal IPv4 or DNS name and port only. IPv6 supported as bracketed literal.
    value = raw.strip()
    match = re.fullmatch(r"([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):([0-9]{1,5})", value)
    if not match:
        raise ValueError("INVALID_WIREGUARD_ENDPOINT")
    hostname, port = match.groups()
    if not 1 <= int(port) <= 65535:
        raise ValueError("INVALID_WIREGUARD_ENDPOINT")
    if hostname.startswith("["):
        try:
            ipaddress.IPv6Address(hostname[1:-1])
        except ValueError as exc:
            raise ValueError("INVALID_WIREGUARD_ENDPOINT") from exc
    elif hostname.startswith("-") or hostname.endswith(".") or ".." in hostname:
        raise ValueError("INVALID_WIREGUARD_ENDPOINT")
    return value


def render_config(env: dict[str, str]) -> str:
    private = check_key(env.get("COUPANG_WG_PRIVATE_KEY", ""))
    public = check_key(env.get("COUPANG_WG_PEER_PUBLIC_KEY", ""))
    endpoint = check_endpoint(env.get("COUPANG_WG_ENDPOINT", ""))
    try:
        address = ipaddress.IPv4Interface(env.get("COUPANG_WG_ADDRESS", ""))
    except ValueError as exc:
        raise ValueError("INVALID_WIREGUARD_ADDRESS") from exc
    if not address.ip.is_private:
        raise ValueError("WIREGUARD_ADDRESS_MUST_BE_PRIVATE")
    return (
        "[Interface]\n"
        f"PrivateKey = {private}\n"
        f"Address = {address}\n"
        "Table = off\n\n"
        "[Peer]\n"
        f"PublicKey = {public}\n"
        f"Endpoint = {endpoint}\n"
        "AllowedIPs = 0.0.0.0/0\n"
        "PersistentKeepalive = 25\n"
    )


def resolve_target_ips() -> list[str]:
    found: set[str] = set()
    for family, _, _, _, address in socket.getaddrinfo(
        HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
    ):
        if family == socket.AF_INET:
            found.add(str(ipaddress.IPv4Address(address[0])))
    if not found or len(found) > 16:
        raise ValueError("TARGET_DNS_UNEXPECTED")
    return sorted(found)


def generate_config(output: Path) -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise ValueError("GITHUB_LINUX_ONLY")
    text = render_config(os.environ)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)
    print("WIREGUARD_CONFIG_READY")


def add_partner_routes() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise ValueError("GITHUB_LINUX_ONLY")
    targets = resolve_target_ips()
    for target in targets:
        subprocess.run(["sudo", "ip", "route", "replace", target + "/32", "dev", "wg0"], check=True)
    print("PARTNERS_IPV4_ROUTES_READY", len(targets))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("config", "route"))
    parser.add_argument("--out", type=Path, default=Path("/tmp/coupang-wg0.conf"))
    args = parser.parse_args()
    try:
        if args.command == "config":
            generate_config(args.out)
        else:
            add_partner_routes()
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch("[A-Z_]+", str(exc)) else "WIREGUARD_PREPARATION_FAILED"
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
