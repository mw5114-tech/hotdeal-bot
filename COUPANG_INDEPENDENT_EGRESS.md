# GitHub-only browser automation with an independently provided outbound network

This is a **configuration and diagnostic harness**. It does not create an ISP connection.
The public GitHub runner already has internet access but does not possess a home/mobile
network interface. To use a different egress IP, an actual *authorized proxy endpoint*
is required (a separately operated connection or a suitable managed service).
An ordinary cloud proxy may face the same website restrictions.

## What it does

1. On a manually started GitHub Actions workflow, obtains the normal GitHub egress IP.
2. Runs Chromium via a configured external HTTP(S) or SOCKS5 proxy.
3. Confirms the proxy's observed public IP from an independent IP-check site.
4. Checks that the two egress addresses differ.
5. If different, makes **one unauthenticated public** visit to partners.coupang.com.
6. Distinguishes valid content from HTTP 200 "Access Denied" and HTTP 403.

No account login, session import, affiliate link conversion, CAPTCHA work, site
authentication, scraping, retry loops, or scheduled publishing is performed.

## Set up when you have a real proxy endpoint

Settings > Secrets and variables > Actions > New repository secret.

- `EGRESS_PROXY_SERVER`: `http://hostname:port`,
  `https://hostname:port`, or `socks5://hostname:port`
- `EGRESS_PROXY_USER` and `EGRESS_PROXY_PASSWORD`: optional matching
  authenticated proxy credentials (separate values). No credentials in URL.

Actions > **GitHub independent proxy egress verification** > Run workflow.

For security, do not share proxy credentials through chat or commit them to code.
Ensure you trust and are authorized to use the external endpoint. Some authenticated
SOCKS proxy implementations may need additional configuration.

## Reading results

- `PROXY_NOT_CONFIGURED`: no real external endpoint supplied.
- `PROXY_EGRESS_IP_UNVERIFIED`: endpoint is not functioning as an outbound proxy.
- `NO_EGRESS_CHANGE`: proxy presents the same external IP as GitHub.
- `PUBLIC_PARTNERS_UI_REACHED`: public first page appeared; **not** a login success.
- `DENIED_BY_SITE`: blocked by the destination (including HTTP 200 denial pages).
- `PUBLIC_RESPONSE_UNCONFIRMED`: public page status not conclusively recognized.

This implementation will not make a proxy service or a residential/mobile network
appear out of thin air. A powered, accessible network gateway or external provider
is the additional prerequisite. If an owner-controlled WAF rule is available, using
an explicit test allow rule is more reliable than trying unrelated cloud egresses.

The preexisting DealSpot / Naver cafe / Telegram workflows remain unchanged.
