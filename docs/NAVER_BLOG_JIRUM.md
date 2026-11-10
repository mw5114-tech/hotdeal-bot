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
- No new poller, login flow, or partner-link policy changes.

## Safety and current state

- Automatic Naver publication was **enabled on 2026-11-09**. Live published
  receipts were confirmed on 2026-11-10 (e.g. Telegram 95553 with product photo);
  future runs still depend on login session and external site availability.
- The link generator may produce an unissued `/re/AFFSDP` candidate.
  It has **not been verified as officially issued or commission-attributed**.
- Disclose Coupang Partners compensation prominently at the beginning of each post.
- Confirm `https://blog.naver.com/mw5114` is registered in Coupang Partners
  as the activity channel before enabling live affiliate posts.
- One FIFO source ID is handled at most once. Claim before submitting; if a
  click outcome is uncertain, stop in REVIEW_REQUIRED instead of retrying.
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
- Do not automatically post every unreviewed Telegram item in bulk to a newly
  created Naver Blog. Platform search/promotion policies may restrict mass
  templated affiliate posts.
