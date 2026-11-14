# Growth Lab — 3 independent Coupang Partners projects

This directory adds **three independent GitHub Actions workflows**, none
modifying or invoking the Telegram Oracle/Daangn queues or Goldbox's existing
15-minute SmartEditor publication flow.

All three use the owner's existing official Partners **Access Key / Secret
Key**, run without browser login, and are bounded by conservative API calls.

| # | Workflow | KST schedule | Source API | Private/public output |
|---|---|---|---|---|
| 1 | `growth-revenue-intelligence.yml` | Daily 16:25 | Click / order / cancel / commission reports | **AES-256-GCM encrypted** metrics |
| 2 | `growth-category-price-radar.yml` | Daily 09:35 and 19:35 | Best-category product feed, 2 category calls/run | Public product-price observations + price-drop alerts |
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

## 2 — Category-best price drop intelligence

Script: `growth_lab/price_radar.py`

Rotates through six Partners category-best IDs: digital, household, kitchen,
pets, food and interior; calls 2 IDs per scheduled run.
It stores one daily price point per **productId:itemId** rather than treating
a productId-only average as a comparable option.

An event is recorded **only if** there are at least 3 distinct earlier daily
observations spanning at least 4 days, and the new same-option price is
20–70% below the earlier median. Unverifiable itemId URLs are skipped.
A previously alerted product won't repeat an event unless another >=5% price
reduction occurs.

Public product-only state and alerts:

- `api-radar-data/state/growth_lab_category_prices.json`

This is **read-only market monitoring**: it does not automatically post to
Naver. The next stage can feed high-confidence alerts into a separately
approved publishing or manual review queue. No duplicate blog content is
created by this project.

## 3 — Search-intent evergreen comparison draft builder

Script: `growth_lab/seo_drafts.py`

Rotates eight practical shopping themes daily (e.g. keyboard, kettle, SSD,
humidifier, monitor arm). Calls one official Partners search endpoint (up to
ten hits), then validates at least three distinct product records and issues
up to three official tracked deeplinks.

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
