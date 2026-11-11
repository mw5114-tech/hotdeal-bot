# Jirum Telegram → Naver Blog integration

Target: https://blog.naver.com/mw5114

## Current deployment (2026-11-10)

- **Main**: `.github/workflows/naver-blog-auto-main.yml` (**NAVER BLOG | MAIN**).
  Oracle `workflow_dispatch` + UTC :17/:47 backup cron + safe smoke + reusable workflow.
- **Legacy compatibility**: `.github/workflows/daangn-jirum-manual-fifo.yml` is now
  **forward-only**; old Oracle servers invoking its filename transparently call
  the Naver main. No Daangn scan or publication job runs.
- **Archived Daangn**: `archive/daangn/daangn-fifo-paused.yml`, completely outside
  `.github/workflows`; see `archive/daangn/README.md` before restoration.
- The GitHub copy of `jirum_github_dispatch.py` now targets the new Naver filename.
  Oracle's currently running copy is NOT updated just by this commit, which is
  why the legacy forwarder remains.
- Naver state remains `state/naver_blog_jirum_fifo.json` (no reset or replay).
  Daangn state remains `state/daangn_jirum_manual_fifo.json` (untouched).
- Shared Telegram source and existing Lowchart/Fallcent product, price and photo
  modules remain imported in place. Naver subid remains `naverblog`.
- **2026-11-11 official API migration**: `COUPANG_PARTNERS_ACCESS_KEY` and
  `COUPANG_PARTNERS_SECRET_KEY` are GitHub Actions Secrets, only passed to
  the Naver job. Verified exact Coupang item/option URLs are submitted to
  official HMAC-SHA256 Deep Link API with `subId=naverblog`. The exact
  `shortenUrl` response is published as a single native Naver hyperlink.
  Neither API keys nor signatures are committed or logged.
  The real response's `landingUrl` may be a Coupang-owned `/re/AFFSDP`
  tracking URL, **but the publisher must still use only its API-issued
  `shortenUrl`**. Verify the landing tracking URL's `pageKey`, `itemId`
  and `subid` against the exact product and `naverblog`; reject any
  explicit mismatches.
- There is **no synthetic AFFSDP fallback** in the Naver publication path.
  API unavailability, missing secrets, wrong option, or unexpected returned
  link prevents publication; the queued item stays unclaimed for the next
  eligible trigger rather than publishing an unissued URL.
- No new poller, login flow, or partner-link policy changes.

## Safety and current state

- Automatic Naver publication was **enabled on 2026-11-09**. Live published
  receipts were confirmed on 2026-11-10 (e.g. Telegram 95553 with product photo);
  future runs still depend on login session and external site availability.
- Legacy enrichment may still prepare an internal unissued `/re/AFFSDP`
  candidate, but it is validated, replaced with an **API-issued link**, and
  explicitly rejected before posting if the API step did not succeed.
  An API-issued link does not itself prove commission attribution; use
  the actual Coupang Partners reports to confirm earnings.
- Disclose Coupang Partners compensation prominently at the beginning of each post.
- Confirm `https://blog.naver.com/mw5114` is registered in Coupang Partners
  as the activity channel before enabling live affiliate posts.
- One FIFO source ID is handled at most once. Claim before submitting; if a
  click outcome is uncertain, stop in REVIEW_REQUIRED instead of retrying.
- Each main workflow run scans once and publishes up to **four** eligible sources
  **sequentially, oldest Telegram ID first**, with at least 10 seconds between
  confirmed posts. One runner installs Chromium once; each source has its own
  draft/photo folder. Batches stop before exceeding a 10-minute budget.
- Items expiring while queued are skipped (never posted late). An ambiguous
  submission stops the batch and preserves its no-retry claim.
- After a verified Naver post and successful GitHub state checkpoint, the
  Contents GET endpoint may briefly return an older branch blob. The batch
  waits for up to 12 read attempts (11 seconds of wait) for the *same*
  source ID and URL to appear in persisted `published` state. If not visible,
  it **stops successfully without posting another product** and lets the next
  Oracle event reconcile the state; it never retries the just-submitted post.
  A mismatched published URL or foreign claim remains a hard safety failure.
- Source timestamp must be fresh (under 60 minutes), with available sale price,
  identified Coupang product/item and a real downloaded product photo.
- Passwords are never stored in the repository; `NAVER_BLOG_STORAGE_STATE_JSON`
  must be an Actions Secret and can expire. 2FA/CAPTCHA is never bypassed.

## Manual diagnostics

1. Open Actions → **Naver Blog - Jirum one-deal preview and guarded test**.
2. Select `preview` to fetch one recent source, build local photo/price/copy,
   and try the Naver editor **without clicking publish**. Inspect logs/artifact.
3. `scan` updates independent Naver source watermark and queue.
4. `post` is intentionally a guarded one-item manual test requiring the
   registered channel and approval of the candidate link format.
5. The new main job has `NAVER_BLOG_PUBLISH_ENABLED: 'true'`; changing it
   to `'false'` pauses only Naver publishing. Daangn cannot run from any
   active publishing workflow and is archived separately.

## Failure handling

- Absence of photo, title, price, source timestamp, or session: no blind submit.
- Signed API integration was separately tested in a **no-blog-post** GitHub
  workflow before enabling this Naver link-only migration. If the API fails,
  stop before a Naver pre-submit claim; do not log raw API responses or
  reinsert a manually constructed affiliate URL.
- After the single final publish click, poll the editor tab, frames and any new
  tab for a verified URL belonging to `mw5114` for up to 15 seconds.
  Never click submit twice; a missing or ambiguous receipt is NOT proof of
  non-publication and stays `REVIEW_REQUIRED` for manual review.
- On a later scan, a completed writer's pre-submit claim older than 30 minutes
  is quarantined as `PUBLISH_OUTCOME_UNCONFIRMED_NO_AUTORETRY`, not reposted.
  This safely unlocks later fresh deals. Do not equate a green scan-only run
  with a published article.
- Do not reset or replay an uncertain source merely because its run failed.
  Reconcile it against actual Naver posts before editing its audit record.
- No extra Oracle poller or scheduled task is installed; the existing dispatch
  and backup schedule are reused.
- This is a **bounded four-item batch**, not unlimited bulk publishing.
  Platform rules may restrict mass templated affiliate posts; review posting
  frequency and the registered Coupang Partners channel.
