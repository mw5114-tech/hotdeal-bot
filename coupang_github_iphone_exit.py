"""Manual-only, GitHub-hosted iPhone Tailscale exit-node access probe.

Runs no browser login, no affiliate link generation and no publishing.
The user's iPhone must be online and approved as an exit node in Tailscale.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def validate_exit_ip(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("TAILSCALE_EXIT_IP_INVALID")
    try:
        address = ipaddress.IPv4Address(raw.strip())
    except ipaddress.AddressValueError as exc:
        raise ValueError("TAILSCALE_EXIT_IP_INVALID") from exc
    if address not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise ValueError("TAILSCALE_EXIT_IP_OUTSIDE_CGNAT_RANGE")
    return str(address)


def status_exit_ready(status: dict) -> bool:
    """Tailscale LocalAPI status: a selected online exit node must be present."""
    if not isinstance(status, dict):
        return False
    exit_status = status.get("ExitNodeStatus")
    return bool(
        isinstance(exit_status, dict)
        and exit_status.get("Online") is True
        and exit_status.get("ID")
    )


def classify_report(report: dict) -> str:
    """A 200 homepage is only web reachability, not an authenticated session."""
    browser = report.get("browser_http", {})
    status = browser.get("main_status")
    if (
        browser.get("result") == "BROWSER_RESPONSE"
        and isinstance(status, int)
        and 200 <= status < 300
        and browser.get("final_host") == "partners.coupang.com"
        and not browser.get("signals", {}).get("deny_or_challenge_marker", False)
    ):
        return "PARTNERS_HOME_REACHED_NOT_AUTHENTICATED"
    return "PARTNERS_STILL_UNREACHABLE"


def run_setup() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux":
        raise ValueError("GITHUB_LINUX_REQUIRED")
    exit_ip = validate_exit_ip(os.environ.get("TAILSCALE_IPHONE_EXIT_IP", ""))
    subprocess.run(["sudo", "tailscale", "set", "--exit-node=" + exit_ip], check=True)
    status_proc = subprocess.run(
        ["tailscale", "status", "--json"], capture_output=True, text=True,
        check=True, timeout=15,
    )
    if not status_exit_ready(json.loads(status_proc.stdout)):
        raise ValueError("TAILSCALE_EXIT_NODE_NOT_ONLINE")
    print("IPHONE_TAILSCALE_EXIT_NODE_SELECTED")


def run_verify(path: Path) -> None:
    report = json.loads(path.read_text(encoding="utf-8"))
    classification = classify_report(report)
    print(classification)
    if classification != "PARTNERS_HOME_REACHED_NOT_AUTHENTICATED":
        raise ValueError("PARTNERS_STILL_UNREACHABLE")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("setup", "verify"))
    parser.add_argument("--report", type=Path, default=Path("coupang-github-transport-report.json"))
    args = parser.parse_args()
    try:
        if args.command == "setup":
            run_setup()
        else:
            run_verify(args.report)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]+", str(exc)) else "IPHONE_EXIT_PROBE_FAILED"
        print(reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
