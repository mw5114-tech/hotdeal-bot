"""Real Chromium -> local controlled HTTP proxy integration test for GitHub Actions.

No external HTTP requests by the test browser. A fake reserved .invalid host
is deliberately requested via a temporary local proxy. Nothing here creates
or simulates a real residential ISP or reaches Coupang.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from coupang_independent_proxy_probe import proxy_config

USERNAME = "local-test-user"
PASSWORD = "local-test-pass"
CREDENTIAL = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
HOST = "proxy-transport-test.invalid"
MARKER = "LOCAL_PROXY_ROUTE_VERIFIED"


class ProxyProbeHandler(BaseHTTPRequestHandler):
    requests_seen = 0
    authorized_requests = 0

    def do_GET(self):
        type(self).requests_seen += 1
        # This proxy only serves a reserved, non-public test hostname.
        if urlsplit(self.path).hostname != HOST:
            self.send_error(403)
            return

        if self.headers.get("Proxy-Authorization") != CREDENTIAL:
            self.send_response(407)
            self.send_header("Proxy-Authenticate", 'Basic realm="local-diagnostic"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        type(self).authorized_requests += 1
        response = f"<html><body>{MARKER}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):
        # Never write request headers including credentials.
        return


def main():
    from playwright.sync_api import sync_playwright
    ProxyProbeHandler.requests_seen = 0
    ProxyProbeHandler.authorized_requests = 0

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyProbeHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        server_address = f"http://127.0.0.1:{server.server_address[1]}"
        config = proxy_config(server_address, USERNAME, PASSWORD)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, proxy=config, timeout=30000)
            try:
                context = browser.new_context(accept_downloads=False)
                try:
                    page = context.new_page()
                    response = page.goto(
                        f"http://{HOST}/through-controlled-local-proxy",
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                    status = response.status if response else None
                    success = (
                        status == 200
                        and MARKER in page.locator("body").inner_text(timeout=4000)
                        and ProxyProbeHandler.authorized_requests == 1
                    )
                finally:
                    context.close()
            finally:
                browser.close()

        if not success:
            raise AssertionError("LOCAL_PROXY_ROUTING_NOT_VERIFIED")
        print(
            "LOCAL_HTTP_PROXY_TRANSPORT_VERIFIED "
            "(Chromium and proxy credentials, using .invalid test host only)"
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
