# Jirum Telegram → Naver Blog integration

Target: https://blog.naver.com/mw5114

## Architecture

- Reuse the existing Oracle event dispatch (which invokes `.github/workflows/daangn-jirum-manual-fifo.yml`).
- Add a separate `naver-blog-fifo` job under the same event + 30-minute backup workflow.
- Never change the Daangn published-state file, publication pause, or Telegram watcher.
- Naver has its own checkpoint `state/naver_blog_jirum_fifo.json`, initially
  baselined at source ID 95495. Older Telegram posts will not be backfilled.
- Source is public `@jirum`, product/option identity and price/photo enrichment
  are reused from the existing Daangn pipeline.
- SubID is `naverblog` to separate commission attribution from `daangncafe`.

## Safety and current state

- Automatic Naver publication is **PAUSED** until a real fresh Telegram deal
  passes SmartEditor title + body + product image dry run.
- The public test post confirmed basic login and text-only publication;
  an image-backed affiliate post is **not yet verified**.
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
5. The existing Oracle-triggered job currently has
   `NAVER_BLOG_PUBLISH_ENABLED: 'false'`. Change to `'true'` only after
   image/upload and real one-item publication have been verified.

## Failure handling

- Absence of photo, title, price, source timestamp, or session: no blind submit.
- Any uncertain submission: leave pre-claim recorded, require manual review.
- No extra Oracle poller or scheduled task is installed; the existing dispatch
  and backup schedule are reused.
- Do not automatically post every unreviewed Telegram item in bulk to a newly
  created Naver Blog. Platform search/promotion policies may restrict mass
  templated affiliate posts.
