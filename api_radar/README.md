# Coupang Partners API Radar — isolated discovery and guarded Naver publisher

## Independent architecture

This workflow is separate from the existing Telegram -> Oracle -> Naver Blog
publisher. It never modifies those original workflows, archived Daangn code
or the original Naver FIFO. A separate branch (`api-radar-data`) holds only
public product/price history and this radar's own publish status.

- Workflow: `.github/workflows/coupang-api-radar.yml`
- Coupang collector: `api_radar/collector.py`
- Public product-state persistence: `api_radar/state_store.py`
- Optional official Naver shopping-click trend: `api_radar/trend.py`
- Exact product/option + Lowchart + official image validation: `api_radar/quality.py`
- Gated, claim-aware (target three verified posts/day) Naver publisher: `api_radar/publisher.py`
- Tests: `api_radar/test_collector.py`, `test_state_store.py`,
  `test_quality_pipeline.py`

## Actual verification (2026-11-14)

- Goldbox: successful authenticated response.
- Product search: the earlier 401 came from signing the URL's question-mark
  delimiter. Fixed HMAC to sign path+query without the '?' character.
  A live one-call search probe returned **10 results** without posting.
- Isolated integration run: **34 raw products** from Goldbox + keyword search,
  with API error count 0. Product history written successfully to the
  `api-radar-data` branch.
- Unit tests: **37 offline tests passed** after A/B publishing integration. Live B-type Naver publication remains unverified until a scheduled run confirms a receipt.
- Product searches may return images at `ads-partners.coupang.com` with
  two opaque URL path segments (including base64 `=` padding). The allowlist
  accepts only exact official host/path shapes, and download verifies image
  MIME, file magic and size with redirects disabled.

## Schedule and API limits

Goldbox's public promotion introduces new daily offers around **07:00 KST**
(Coupang newsroom); this does **not** guarantee that 100% of products are
replaced at once. The first scheduled scan follows at 08:05.

The independent workflow runs **08:05, 12:10, 16:25, 20:40 KST**, or on manual
`workflow_dispatch`. GitHub cron can be delayed. It queries Goldbox once
and one rotating keyword search at most once per run. On 429/error it stops
the failed source rather than hammering the endpoint. Check current Coupang
Partners quota in your partner account; older official guidance stated
Search <= 10 calls/hour.

GitHub Actions `push` runs only offline unit tests. They do not consume
Partners quota or open the blog editor.

## Publication conditions (fail closed)

The publisher job runs after a completed discovery job and shares the exact
Naver concurrency group with the existing Telegram publisher. It aims for
**up to 3 posts combined across both grades per KST day**, examining up to
**36 fresh candidates** (at most 12 Lowchart history checks) and submitting
at most **2 posts per run**, and uses the
existing Naver SmartEditor session. It maintains separate durable state and
receipts. Today's confirmed posts AND uncertain in-flight claims count toward
the daily safety cap; no uncertain submissions are retried. **Three posts are
NOT guaranteed**: products failing verification are skipped.

### Grade A: independently verified discounted deals (first priority)

- Recent curated shopping keyword or vetted category.
- Exact Coupang API productId+itemId.
- Live same-option Lowchart historical **average** with within-2% current
  price corroboration, valid title/identifiers, and **15–70%** savings relative
  to that observed average. No fabricated MSRP or implied regular list price.
- When optional `API_RADAR_TREND_MODE: hub` is enabled, an independently
  verified rising relative Shopping Insight click trend is also required.
- Title: `[쿠팡🔥핫딜]`. Only this grade publishes a sourced discount percentage.

### Grade B: official Goldbox feed (fallback when grade A runs short)

- Candidate must come from the **official authenticated Goldbox API**, never
  merely a keyword search, and be from the latest fresh scan.
- Only the vetted physical-shopping categories, with the observed API sale
  price within 5,000–350,000 KRW. Exact productId+itemId is still mandatory.
- No Lowchart reference/discount is required or claimed; the visible post says
  `수집 당시 쿠팡 판매가` and explains that past-average savings are unverified.
- Title: `[쿠팡🎁골드박스]`. Note that Goldbox benefits may depend on Wow
  membership and price/stock vary.
- Grade B is considered only AFTER the bounded grade A checks; it may use a
  candidate that failed grade A due to insufficient verified history.

### Gates shared by both grades

- Safe official Coupang product URL with exact option identity; never click
  opaque affiliate tracking links for verification.
- Verified official product-image bytes (size, MIME and redirects restricted).
- Fresh official Coupang Partners Deep Link API response with `subid=naverblog`,
  never a handcrafted affiliate URL.
- No previous publication or pending uncertain claim for that product;
  KST daily cap counts both confirmed posts and unresolved claims.
- Verified disclosure and affiliate hyperlink in the post, no synthetic first-
  hand review, duplicate product, unverified discount or false search demand.

The publisher stores a durable claim BEFORE SmartEditor submits. An uncertain
outcome requires manual review and is never automatically retried.

Shopping Insight, when explicitly configured, is a **relative click index**,
not absolute search volume or a guarantee of blog visitors.

The publisher stores a claim in the independent branch BEFORE clicking the
Naver submit button. An uncertain failure leaves the claim for manual review:
the system never automatically retries that product.

## Required credentials (repository Actions Secrets)

The existing repository is public. **Never commit credentials or session
JSON files.** Use GitHub Settings -> Secrets and variables -> Actions:

- `COUPANG_PARTNERS_ACCESS_KEY`: already used by the existing collector.
- `COUPANG_PARTNERS_SECRET_KEY`: already used by the existing collector.
- `NAVER_BLOG_STORAGE_STATE_JSON`: existing encrypted browser session secret.
- `NAVER_API_HUB_KEY_ID`: OPTIONAL; required only if switching `API_RADAR_TREND_MODE` to `hub`.
- `NAVER_API_HUB_KEY`: OPTIONAL; required only when using optional paid/cloud shopping trends.

Naver API HUB keys are **not** the same as a Naver account password or
the old developers.naver.com API keys. Register and enable the Shopping
Insight API through Naver Cloud Platform before setting the new secrets.

Official endpoint:
`POST https://naverapihub.apigw.ntruss.com/shopping/v1/category/keywords`

The default `API_RADAR_TREND_MODE: curated` DOES NOT need either NAVER
API HUB secret, cloud account or payment method. It uses curated product-name
keywords or vetted official shopping categories, then checks at most twelve
recent grade-A candidates against exact-item Lowchart history before using
truthfully labeled official Goldbox grade-B fallback listings.
It does not claim measured NAVER search trend/click index. The independent
publisher can attempt up to three posts per KST day if all other gates pass.
Set `API_RADAR_TREND_MODE: hub` only after registering both Naver API HUB
Secrets to enable the stricter search-click trend requirement.

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

- Curated keyword/category matches are heuristics, NOT verified NAVER search demand.
- A quota is a ceiling, not a promise of three posts. We do not invent price history
  or discounts merely to reach a publishing target. Grade B has no independently
  verified discount percentage, only official Goldbox listing membership.
- Naver's official terms restrict posting by bots without prior permission and
  repetitive affiliate-style content may have its visibility limited; verify
  authorization and follow applicable platform policies before scaling.
- Optional Naver API HUB trends are topic-level, not product-specific traffic forecasts.
- ProductId snapshots alone do not establish option-level price history.
- Cross-source duplicate detection with existing Telegram posts is not
  guaranteed; this publisher deduplicates within the independent radar.
- NAVER's indexing/ranking and click-to-purchase results cannot be promised.
- This integration does not yet include a complete affiliate revenue
  analytics/AB-test feedback loop.
