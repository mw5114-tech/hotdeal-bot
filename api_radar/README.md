# Coupang Partners API Radar — Goldbox daily A/B queue

## Goal and isolation

Detect each newly captured **official Coupang Goldbox** product, verify its
exact product and option, publish A when Lowchart independently confirms
a genuine historical-average discount of 15–70%, and otherwise publish B
as an **official Goldbox listing without a fabricated discount percentage**.

There is **no per-day post count quota**. Each publish-only Actions run
attempts **at most one** post, and a persisted **minimum 900-second gap**
is enforced after the latest confirmed post OR uncertain Naver submission
claim. Publish-only schedules resume remaining items every 15 minutes. These are operational limits,
NOT a guarantee that all products will be published. Items failing identity,
image, link, stale-price or platform checks cannot safely be posted.

The system is independent of the pre-existing Telegram → Oracle → Naver Blog
FIFO and its workflows. It never modifies the original FIFO or archived
Daangn modules. Product-price history and Goldbox daily inventory are stored
on the public **api-radar-data** branch at
`state/api_radar_snapshot.json`; no credentials or login sessions are stored.

## Goldbox collection and day-to-day duplicates

- Workflow: `.github/workflows/coupang-api-radar.yml`
- Collector: `api_radar/collector.py`; Goldbox API + a separate rotating
  keyword-search observation for price history (search-only products are
  **NOT** posted by the Goldbox queue)
- State writer: `api_radar/state_store.py`
- Publisher: `api_radar/publisher.py`
- Exact item/history/image checks: `api_radar/quality.py`
- Current day inventory: `goldbox_today = {kst_date, updated_at, items}`
- Per-product publish receipts: `published[productId]`
- Pending uncertain outcomes: `api_publish_claims[productId]`

Goldbox scans run **08:05 / 12:10 / 16:25 / 20:40 KST** (GitHub Actions
cron may run late). The source's new products appear in the morning; its
entire previous listing is NOT guaranteed to change on the same minute.
Snapshots within a KST day merge newly observed products. On the next KST
day, the current-day inventory resets, while all **published** IDs and
uncertain claims persist. A previously published product cannot be posted
again merely because the Goldbox feed lists it on a later day.

Publish-only runs are scheduled every **15 minutes from 08:22 to 23:52 KST**
(08:22, 08:37, 08:52, 09:07, 09:22, ...). These do **not** call the Coupang
Partners product discovery API. A lightweight queue preflight prevents browser
installation when the backlog is empty or the 900-second cooldown has not
expired. GitHub cron can be delayed or dropped: do not promise exact-time
publication or that all daily products will be published.
The publisher checks only same-KST-day products whose most recent observed
price is within twelve hours; expired offers are never described as today's
current promotions.

## Grade classification

For **each current-day official Goldbox candidate**:

1. Validate official HTTPS URL, exact productId + itemId, reasonable
   observed price, non-duplicate product ID and verified image.
2. Attempt exact-option Lowchart history verification. A matching observed
   current price (within 2%) and average showing **15–70% lower than the
   observed historical average** earns **A**. Only A may show the
   source-backed percentage and the corresponding reference period.
3. If historical evidence is missing, conflicts, or is less than 15%,
   use **B** based on independently authenticated current-day Goldbox
   membership. B shows the **observed API sale price** and prominently
   states that its past-average discount percentage is *unverified*.
4. In both cases, validate source image bytes, issue a real signed Coupang
   Partners Deeplink (`subid=naverblog`), include commission disclosure,
   and submit through the existing Naver SmartEditor session.
5. Write an immutable GitHub claim *before* the Naver submit. Record the
   confirmed post URL and grade only after successful submission. If the
   outcome is unknown, **never automatically retry that product**.

Title prefixes:

- A: `[쿠팡🔥핫딜]`
- B: `[쿠팡🎁골드박스]`

Non-physical travel/ticket/gift-card categories, malformed offers, missing
exact item IDs, invalid images, or unverified affiliate URLs are skipped,
not force-published. ProductId-level price observations alone do not establish
option-level historical savings.

## Operational safeguards and limits

- **No daily numeric publishing quota.** At most one publish call per
  15-minute invocation. A real 900-second cooldown, persisted across
  different GitHub runs, includes uncertain submission claims.
- Avoid duplicate publications across scans/days with global productId
  receipts and durable unknown-outcome claims.
- Same Naver publish concurrency mutex as Telegram publisher.
- No fake ratings, first-hand reviews, absolute search volume, manufacturer
  list price or invented discount.
- Official API requests are bounded and stop on API rate limits.
- The public GitHub code and state may be viewed by anyone; store Keys
  and Naver login browser JSON only in Actions Secrets.
- The **actual Naver platform may limit repetitive or bot-generated posts**;
  permission and platform policy must be respected. Neither search indexing
  nor exhaustive daily posting can be promised.

## Required Actions Secrets

- `COUPANG_PARTNERS_ACCESS_KEY`
- `COUPANG_PARTNERS_SECRET_KEY`
- `NAVER_BLOG_STORAGE_STATE_JSON`

Optional (not needed in free `curated` mode): `NAVER_API_HUB_KEY_ID`,
`NAVER_API_HUB_KEY`. Shopping Insight provides relative click indices, not
verified absolute Naver keyword-search volumes.

## Verification

As of 2026-11-14, public data reads, the verified Goldbox collector, exact
source-image normalization and the offline API Radar tests have succeeded.
The new day-based A/B queue requires **a confirmed scheduled collector and
real publication run** before claiming end-to-end production success.
A GitHub Actions `push` triggers offline tests only; it never calls the
live Coupang API or opens Naver's editor.

Observe `api-radar-data/state/api_radar_snapshot.json`, workflow logs and
the enabled hourly confirmed-post notification for actual outcomes.
