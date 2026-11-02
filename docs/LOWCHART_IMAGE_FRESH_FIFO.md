# Jirum → Daangn: Kakao-free exact-item Lowchart photos (2026-11-02)

## What runs

`.github/workflows/daangn-jirum-manual-fifo.yml` remains on the existing
five-minute cron and manual `workflow_dispatch`. It now enables:

- `JIRUM_IMAGE_SOURCE=lowchart`
- `JIRUM_REQUIRE_FRESH_POST=true`
- The Kakao login secret is **not mounted** in this publishing workflow.

The workflow still scans the public source Telegram messages, persists
watermark/queue to GitHub state, uses the same own AFFSDP link,
waits for photo uploads, and verifies saved posts. Existing Oracle
Telegram forwarding is untouched.

## Photo selection

1. Follow one known Coupang short-link hop to get `productId` and `itemId`.
2. Request only `https://www.lowchart.com/<productId>-<itemId>` (no login).
3. Require exact ID presence in the public page and a product title matching
   the source name. Additional color/model details can follow an unambiguous
   source-name prefix, but contradictory sizes or counts are rejected.
4. Select exactly one actual HTML `<img>` with matching product-title `alt`;
   **never select Lowchart's price-chart OG graphic or other recommendations**.
5. Require an HTTPS Coupang CDN image host, real image MIME/magic, no redirects,
   a bounded file size and exact source-ID filename.
6. The publisher checks exactly one local picture and link, and waits for the
   upload to finish before submitting; if any verification fails, **no post**.

The existing Lowchart observed average still needs strict product/item/title
and price history verification. This does **not** prove live Coupang stock
or that the Telegram price is still active; those claims are not asserted.

## Freshness policy

A deal is eligible only if its real Telegram per-message UTC timestamp is
within the last **60 minutes** (future skew up to 5 minutes tolerated).
Unknown dates, future dates and older deals are not sent for publication.

At first cutover the legacy pending queue lacks timestamps. The scanner
backfills those it can still see in the current public Telegram window;
the remainder are **archived, not posted**. Evidence is saved under
`skipped_expired` (ID, reason, source URL/title and timestamp).
This avoids publishing dozens of expired prices or resetting previously
published IDs.

Unambiguous, permanent photo/title/option mismatches are independently
archived as `skipped_unverifiable` with the source ID and reason, and the
next queued deal is considered. Transient network errors are **not** silently
archived; they stop for a future retry.

The old `kakao_auth_block` is cleared during scan when Lowchart mode is
enabled. Refreshing Kakao login is no longer necessary for this workflow.

## Verification and monitoring

- CI: `Tests | Jirum manual FIFO (offline only)`
- Manual read-only API check: `smoke_test=true`
- Real publication: `smoke_test=false` or the 5-minute schedule
- Queue: `state/daangn_jirum_manual_fifo.json`
- Safe outputs: `JIRUM_MANUAL_SCAN`, `JIRUM_STALE_BACKLOG_ARCHIVED`,
  `JIRUM_LOWCHART_UNVERIFIABLE_ARCHIVED`, `JIRUM_MANUAL_PUBLISH`
- A successful scheduled run with zero new eligible posts is **expected**.
  It indicates the scan succeeded; it does not prove a cafe post happened.

If Lowchart changes its markup, or a CDN file fails download, the system
must not use a random stock image or bypass item checks. Diagnose the
public-page parser before changing matching rules.

The GitHub repo is private, and five-minute scheduled jobs consume
private GitHub-hosted minutes. Event-driven Oracle dispatch can be used
as the primary trigger after live validation.

## Kakao login question

`KAKAO_DEV_STORAGE_STATE_JSON` is a snapshot of browser session cookies
and storage, **not a renewable password grant**. A cookie may expire or
be invalidated server-side, or the account may require a fresh device/
two-factor login. The observed redirect proves that the stored session
was no longer accepted, but it cannot identify the exact cause.
One can attempt a normal browser login using credentials securely stored
in a server secret, but additional confirmation may still require human
action; the production Lowchart route avoids this dependency altogether.
