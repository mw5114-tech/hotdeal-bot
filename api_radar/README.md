# Coupang Partners API Radar (isolated from Telegram/Naver)

## Current mode: automatic product discovery and price tracking ONLY

The existing Telegram -> Oracle -> GitHub -> Naver Blog publication pipeline
is unchanged. This radar does not access Naver login secrets, browser sessions,
Telegram messages, or any existing FIFO state. It does not publish blog posts.

- Workflow: `.github/workflows/coupang-api-radar.yml`
- API client: `api_radar/collector.py`
- State logic: `api_radar/state_store.py`
- Tests: `api_radar/test_collector.py`, `api_radar/test_state_store.py`
- Isolated Git branch: `api-radar-data`
- Public-market-data-only history: `state/api_radar_snapshot.json` on that branch
- Candidate report: workflow artifact `summary.json` and `ranked.json`

## Schedule

Twice daily (KST): **08:05** and **18:20**. GitHub Actions cron may run late.
Manual workflow dispatch is also possible.

The authenticated Goldbox API is called once per run. Authentication uses only
`COUPANG_PARTNERS_ACCESS_KEY` and `COUPANG_PARTNERS_SECRET_KEY` supplied by
GitHub Actions Secrets; neither appears in the public artifact or saved state.
No browser, Playwright, or additional paid third-party API is required.

## Search API safety gate

Real API testing on 2026-11-13:
- Goldbox: HMAC-authenticated GET succeeded, returned 25 rows, and 23 passed
  strict basic public-product validation.
- Product search: HTTP 401 despite the successful Goldbox request.

Therefore `API_RADAR_SEARCH_ENABLED` defaults to **false**. Do not enable it
before establishing access/signature requirements through the Partners help
docs or support. A 401 is NOT a reason to hammer the endpoint.

## Ranking and price claims

Stored history tracks only the price exposed by the API for a productId on
each KST date. The API does not certify an identical vendor item, option,
membership benefits or stock across calls. This is NOT a verified
same-option average/30-day historical price. The ranking is preliminary, and
`posting_eligible` is always false.

The system explicitly does **not**:
- invent original prices or discount rates;
- claim NAVER shopping search demand without independent evidence;
- synthesize affiliate tracking URLs;
- make false first-hand product reviews;
- generate or publish low-quality duplicate blog posts;
- put secrets or session/cookie state in GitHub artifacts.

The daily data branch avoids triggering or changing the existing main-branch
Telegram, Naver and Daangn workflows. When this radar has reliable stable
option identity, permitted product imagery, validated historical comparison,
and independently measured search intent, a separate gated publisher can be
connected. Before then, it stays read-only with respect to Naver.

## Expected steady-state

One successful API Goldbox call per run, up to 2 per day. The collector
emits a public product-only shortlist with no authorization headers, cookie
values, account identifiers or raw gateway responses. GitHub Actions executes
up to 16 offline unit tests per run. State writes are confined to one JSON
file on the dedicated `api-radar-data` branch.

To inspect results, open the Actions run and download its artifact. To
temporarily stop collection, disable this one workflow; the existing
Telegram/Naver work remains unaffected.
