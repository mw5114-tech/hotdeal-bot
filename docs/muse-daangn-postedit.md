# Historical Muse post-edit handoff — INACTIVE (2026-11-03)

Production now uses the restored /re/AFFSDP candidate link pattern and
does not create new PUBLISHED_AWAITING_MUSE posts. This archived plan is
retained for reference only. No Muse link replacement is required.

GitHub posts a Telegram Jirum deal to Daangn with a plain, clean Coupang product link first. Muse later replaces only that link in the already-published post with the genuine official Coupang Partners /a/ link using the logged-in Partners browser.

## Muse read-only queue

Read state/daangn_jirum_manual_fifo.json on main. Consider ONLY published entries whose status is PUBLISHED_AWAITING_MUSE. Those new entries include the exact published Daangn post_url, posted_at_utc (ORIGINAL Telegram publication time, not the Daangn post time), product_id, item_id and optional vendor_item_id. The original Telegram ID is the key of the published dictionary.

Before editing, verify posted_at_utc exists and now - posted_at_utc < 60 minutes. At >= 60 minutes skip permanently, keep a local Muse-VM audit entry; do not touch that post. Already-old historical entries PUBLISHED_VERIFIED must not be edited. Do not rely on the GitHub publication time for the cutoff.

Open the Daangn post_url, verify the article contains exactly one initial www.coupang.com/vp/products/{product_id}?itemId={item_id} (plus optional vendorItemId) and that it matches the published metadata. Never replace links in a different article or a different product/option.

In the ORIGINAL currently signed-in Coupang Partners tab (no new browser), paste the clean product URL into 간편 링크 만들기, generate an official https://link.coupang.com/a/... URL, and modify ONLY the existing link in the same Daangn post. Preserve title, prices, discount, image, warnings, other content, and commission disclosure. Before clicking Save, recheck the 60-minute age from original Telegram timestamp.

After saving, reload the Daangn post and verify exactly one full official /a/ URL now appears and the plain product link is absent. Record message ID and finished link in Muse's own persistent local audit. On subsequent read-only scans, first inspect whether the post is already rewritten, so a Muse VM restart or missing local log cannot cause duplicate edits or repeated link generation.

If Coupang Partners session expires or Muse browser closes, stop editing and report NEEDS_LOGIN. GitHub continues collecting and publishing ordinary product links, independently; Muse only catches up with posts younger than 60 minutes once re-authenticated. The full pipeline is not guaranteed to run unattended if Muse's scheduled browser execution itself requires approvals.

GitHub connector WRITE is not required and must not be used for this workflow. GitHub's published[*].status remains PUBLISHED_AWAITING_MUSE since Muse has no write-back permission; verify Daangn live post and Muse local audit before attempting an edit. No active web post or Muse login was exercised by this GitHub change.
