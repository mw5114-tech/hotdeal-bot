# Coupang Partners API Radar — isolated discovery and guarded Naver publisher

## Independent architecture

This workflow is separate from the existing Telegram -> Oracle -> Naver Blog
publisher. It never modifies those original workflows, archived Daangn code
or the original Naver FIFO. A separate branch (`api-radar-data`) holds only
public product/price history and this radar's own publish status.

- Workflow: `.github/workflows/coupang-api-radar.yml`
- Coupang collector: `api_radar/collector.py`
- Public product-state persistence: `api_radar/state_store.py`
- Official Naver shopping-click trend: `api_radar/trend.py`
- Exact product/option + Lowchart + official image validation: `api_radar/quality.py`
- Gated one-post Naver publisher: `api_radar/publisher.py`
- Tests: `api_radar/test_collector.py`, `test_state_store.py`,
  `test_quality_pipeline.py`

## Actual verification (2026-11-13)

- Goldbox: successful authenticated response.
- Product search: the earlier 401 came from signing the URL's question-mark
  delimiter. Fixed HMAC to sign path+query without the '?' character.
  A live one-call search probe returned **10 results** without posting.
- Isolated integration run: **34 raw products** from Goldbox + keyword search,
  with API error count 0. Product history written successfully to the
  `api-radar-data` branch.
- Unit tests: 28 offline tests passed at initial publisher integration.
- Product searches may provide images at
  `ads-partners.coupang.com/openapi/image/...`; the allowlist also supports
  Coupang's vetted image CDN host patterns. No arbitrary image URL is fetched.

## Schedule and API limits

The independent workflow runs **08:05** and **18:20 KST**, or on manual
`workflow_dispatch`. GitHub cron can be delayed. It queries Goldbox once
and one rotating keyword search at most once per run. On 429/error it stops
the failed source rather than hammering the endpoint. Check current Coupang
Partners quota in your partner account; older official guidance stated
Search <= 10 calls/hour.

GitHub Actions `push` runs only offline unit tests. They do not consume
Partners quota or open the blog editor.

## Publication conditions (fail closed)

The publisher job runs after a completed discovery job and shares the exact
Naver concurrency group with the existing Telegram publisher. It can publish
at most **1 verified offer per KST day** and uses the existing Naver
SmartEditor session, but maintains its own durable state and receipts.

It ONLY publishes when ALL are true:

1. The owner has stored official NAVER API HUB secrets (below).
2. A recent candidate matches a curated keyword/category and the official
   Shopping Insight **relative shopping-click trend** is rising.
3. The API product URL resolves exclusively through allowlisted Coupang
   domains to an exact productId and itemId (same-option identity).
4. A live Lowchart exact-item page confirms current price within 2% of the
   API quote, visible exact item identifiers/title and a historical average
   at least 15% above the observed price.
5. The official API product-image URL returns valid, bounded image bytes.
6. An authorized Coupang Partners Deep Link response issues a new link
   with `subid=naverblog`. No synthetic AFFSDP URL is accepted.
7. The product has not been posted or permanently claimed by this radar.

The comparison is explicitly labeled **Lowchart observed average**, not
Coupang's official regular price, and no specific 30-day average is claimed
without a verified time window. Shopping Insight is a **relative click index**,
not absolute search volume or a guarantee of blog visitors. Posts identify
the affiliate relationship and never impersonate a first-hand review.

The publisher stores a claim in the independent branch BEFORE clicking the
Naver submit button. An uncertain failure leaves the claim for manual review:
the system never automatically retries that product.

## Required credentials (repository Actions Secrets)

The existing repository is public. **Never commit credentials or session
JSON files.** Use GitHub Settings -> Secrets and variables -> Actions:

- `COUPANG_PARTNERS_ACCESS_KEY`: already used by the existing collector.
- `COUPANG_PARTNERS_SECRET_KEY`: already used by the existing collector.
- `NAVER_BLOG_STORAGE_STATE_JSON`: existing encrypted browser session secret.
- `NAVER_API_HUB_KEY_ID`: NEW, official Naver Cloud NAVER API HUB Client ID.
- `NAVER_API_HUB_KEY`: NEW, corresponding NAVER API HUB Client Secret.

Naver API HUB keys are **not** the same as a Naver account password or
the old developers.naver.com API keys. Register and enable the Shopping
Insight API through Naver Cloud Platform before setting the new secrets.

Official endpoint:
`POST https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords`

Until both NAVER API HUB secrets are configured, the publish job skips the
browser and makes **zero blog posts**, while Coupang discovery and
price-history tracking continue normally. Once they are configured,
guarded publishing may activate automatically under these restrictions.

## Operations

Results are in each run's short-lived artifact (`summary.json`,
`ranked.json`). Long-term public product-only observations are in
`api-radar-data/state/api_radar_snapshot.json`. These do not contain
account passwords, private cookies, or API credentials.

If a post is uncertain, inspect `api_publish_claims` on the data branch;
**do not delete claims** until confirming the exact Naver post exists or not.
To pause only the new radar, disable this workflow. It cannot interrupt
or disable the existing Telegram Naver flow.

A missing Naver API HUB key or weak trend is **not** evidence that the
Coupang API integration is broken. The collector and publisher can be
operational while producing zero posts because the quality gates are strict.

## Known limits / next optimization

- Search trends are topic-level, not product-specific traffic forecasts.
- ProductId snapshots alone do not establish option-level price history.
- Cross-source duplicate detection with existing Telegram posts is not
  guaranteed; this publisher deduplicates within the independent radar.
- NAVER's indexing/ranking and click-to-purchase results cannot be promised.
- This integration does not yet include a complete affiliate revenue
  analytics/AB-test feedback loop.
