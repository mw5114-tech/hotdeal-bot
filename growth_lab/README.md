# Growth Lab — 3 independent Coupang Partners projects

This directory adds **three independent GitHub Actions workflows**, none
modifying or invoking the Telegram Oracle/Daangn queues or Goldbox's existing
15-minute SmartEditor publication flow.

All three use the owner's existing official Partners **Access Key / Secret
Key**, run without browser login, and are bounded by conservative API calls.

| # | Workflow | KST schedule | Source API | Private/public output |
|---|---|---|---|---|
| 1 | `growth-revenue-intelligence.yml` | Daily 16:25 | Click / order / cancel / commission reports | **AES-256-GCM encrypted** metrics |
| 2 | `growth-category-price-radar.yml` | Every 2 hours at :35 KST | Best-category feed, 2 calls/run | Same-option 6h ticks, 30d median + 24h price-drop signals |
| 3 | `growth-seo-comparison-drafts.yml` | Daily 11:42 | Keyword product search + up to 3 official deep links | **REVIEW-REQUIRED** comparison Markdown drafts, NOT posted |

A separate `growth-lab-tests.yml` runs offline unit tests only on code pushes
and manual dispatch. Scheduled workflows consume official API quota; offline
push tests do not.

## 1 — Revenue intelligence

Script: `growth_lab/revenue.py`

Uses read-only `/v1/reports/clicks`, `orders`, `cancels`, and
`commission` for the **previous KST date** (reporting update around 15:00).
It creates a date-by-date, source-identified aggregate rather than attempting
to attribute all sales to individual articles.

**Security on a public GitHub repository:** No plaintext revenue, order IDs,
auth headers, cookies, or raw report JSON enter git history, workflow logs,
or Actions artifacts. The only saved state is ciphertext at:

- Branch `api-radar-data`
- Path `state/growth_lab_private_analytics.json`

Encryption derives an AES-256-GCM key from the existing Partners secret via
HKDF-SHA256 with domain separation, using a random nonce for every write and
authentication on decrypt. Changing the Partners secret without migrating the
ciphertext makes old state undecryptable (fail closed).

A 14-day category ranking is calculated *only when* the orders report exposes
category + observed commission. It cannot promise SEO traffic, sales causation,
or post-level attribution with a single shared `naverblog` SUBID.

To view exact figures privately, download the ciphertext JSON using the
GitHub UI and run **locally**, with the correct Partners Secret environment
variable set: `python -m growth_lab.revenue --decrypt-file /local/path.json`.
Do NOT run this command in public GitHub Actions logs.

No separate encryption Secret is required; it reuses the existing Partners
Secret already stored in Actions Secrets.

## 2 — Verified price-drop V2 (owner-approved live switch)

Scripts: growth_lab/price_radar.py and growth_lab/drop_publisher.py.

- Discovery: 2 category-best API reads every 2 hours; all six categories
  covered about every 6 hours; 24 category calls/day. Handles 429 safely.
- History: per productId:itemId, a capped 38-day daily price history and
  intraday ticks; vendorItemId or stable title-option changes reset history.
  Capped at 280 tracked options to stay within GitHub Contents size limits.
- Detection: >=5 distinct prior observation days spanning >=7 days within
  the last 30 days; and a ~24h observation (18-31h) for the exact same option.
  Current price must be >=20% below observed 30-day daily median and >=15%
  below the price observed about 24 hours earlier. S requires >=25% and
  >=20% respectively; other eligible drops are A. Extreme >70% drops are
  quarantined. Neither MSRP nor alleged official discount is fabricated.
- Storage: public product facts only, in branch api-radar-data at
  state/growth_lab_category_prices.json. Signals expire after 120 minutes.
- Publisher: the EXISTING Goldbox 15-minute Naver job switches to
  API_RADAR_VERIFIED_ONLY=true and runs the V2 bridge. It re-queries the
  same official best-category feed at publish time, requiring a fresh exact
  product, item, vendor, title signature, and unchanged price.
- Shared posting safeguards: 15-minute cooldown, published/claim records,
  uncertain-claim quarantine, a single browser mutex, and official naverblog
  affiliate links all remain in the original api_radar state. Only one
  verified signal per slot can publish; old unverified Goldbox B products
  no longer auto-publish. Goldbox product collection still runs.
- Untouched modules: Telegram->Naver, Daangn, Growth Lab revenue analysis,
  and SEO review-required drafts.

Warmup is normal: no verified drop can be claimed on the first observed
day. Category-best membership, member/coupon/checkout differences, and cron
delays mean this radar cannot discover every online deal.


## 3 — Search-intent evergreen comparison draft builder

Script: `growth_lab/seo_drafts.py`

Rotates eight practical shopping themes daily (e.g. keyboard, kettle, SSD,
humidifier, monitor arm). Calls one official Partners search endpoint (up to
ten hits), then validates at least three distinct product records and issues
up to three official tracked deeplinks. When a SEARCH URL hides the exact
itemId, the draft uses an API-issued generic product-level deeplink and
**explicitly warns that API-listed price may not match the buyer's selected
option**. Neither itemId nor equivalent options are invented.

Each independent Markdown draft has:
- An accurate affiliate-commission disclosure.
- Purchase criteria (clearly identified as checks, **not verified specs**).
- Three actual API-seen product names and **observed** API sale prices.
- Available rocket/free-shipping flags and valid official affiliate links.
- An explicit warning that option sizes, specs, price and stock can differ.
- No firsthand review, fake ranking, unsourced discount, made-up search
  volume, fabricated traffic, or false same-option equivalence.

Output:
- `api-radar-data/state/growth_lab_seo_drafts.json`
- Each draft has `status=DRAFT_REVIEW_REQUIRED`, `auto_published=false`.

**Why drafting, not automatic publishing?** Evergreen comparisons demand
human editorial verification of specifications and search intent. Publishing
repetitive or unverified affiliate posts can breach platform policies. This
workflow is fully automatic **through collection, sourcing, link issuance
and draft storage**, but does not operate SmartEditor or click Publish.

## Initial activation / operational requirements

Required existing Actions Secrets:
- `COUPANG_PARTNERS_ACCESS_KEY`
- `COUPANG_PARTNERS_SECRET_KEY`

The repo's built-in `GITHUB_TOKEN` must be allowed to write Contents to
the existing `api-radar-data` branch. Distinct file paths prevent conflicts
with existing Goldbox and Telegram state.

The reporting job installs `cryptography` as its only extra Python package.
The price and SEO jobs need Python 3.11 with stdlib only.

Each scheduled workflow uses a distinct concurrency group and has a
bounded runner timeout. These schedules are *not proof of live API success*;
API permissions, option identity availability, rate limits and GitHub cron
delays must be checked in their individual Actions logs.

This implementation does not automatically notify ChatGPT. Future
notifications should only be sent on actually confirmed new price events
or report changes, not on every scheduled run.
