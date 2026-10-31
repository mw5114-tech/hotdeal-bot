"""Preflight test: can GitHub reach an official proxy provider's entry node?

This sends no proxy credentials, browser request, or request to any target website.
No assumption is made about having an active proxy subscription.
"""
import json
import socket

HOST = "gate.decodo.com"
PORT = 7000


def check():
    result = {"provider": "Decodo", "gateway": HOST, "port": PORT,
              "authenticated": False, "proxy_ip_provided": False}
    try:
        addresses = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
        result["dns_resolved"] = len(addresses) > 0
    except OSError as exc:
        result["dns_resolved"] = False
        result["dns_error_type"] = type(exc).__name__
        return result
    try:
        with socket.create_connection((HOST, PORT), timeout=8):
            result["tcp_reachable"] = True
    except OSError as exc:
        result["tcp_reachable"] = False
        result["tcp_error_type"] = type(exc).__name__
    return result


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    if not result.get("tcp_reachable"):
        raise SystemExit(1)
