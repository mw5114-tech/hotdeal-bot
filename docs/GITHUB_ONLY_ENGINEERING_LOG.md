# GitHub-only Deal Automation — Engineering Log

Experimental branch: test/github-only-pipeline-20261031
Goal: PC off, no paid third-party product API, no Muse VM dependency.
Production main and existing 30-minute GitHub jobs are untouched.

## 2026-10-31 iteration log

| Iteration | Real result | Decision |
| --- | --- | --- |
| 0, previous browser experiments | Linux and Windows Playwright Chrome/Firefox/WebKit/Cromium all showed Coupang product access-denied, including one HTTP 200 denial page | Stop direct-product-page retry loops; 200 is not evidence of product data |
| 1, GitHub run 41206288785 | 18 offline unit tests passed. Live one-request public Telegram scan succeeded: 8 posts, 6 sale-price candidates, 7 original-price candidates, zero images | Prefer accessible source-post prices to unavailable Coupang HTML |
| 2, GitHub run 41206497163 | Photo-wrap DOM elements 0, link-preview image elements 3 | Distinguish attached photos, product preview image, and user avatar |
| 3, GitHub runs 41206590828 and 41206610135 | New fallback image test failed due fixture not matching actual image CSS class | Preserve CI failure, correct fixture and retest |
| 4, GitHub run 41206738719 | 8 live posts; 6 sale prices, 7 original-price candidates, 2 link-preview image candidates | Provisional source image only; never assert exact product image |
| 5, GitHub run 41206892873 | Six Coupang-shortlink posts had no image candidates in this sample; two external-link posts had previews | Images remain the key unresolved data source |
| 6, GitHub run 41207144011 | 27 offline tests passed including approved Partners API signing, request guards, mock deeplink response | Integration exists offline only, real keys unavailable |
| 7, current branch | Telegram user-session media adapter with offline tests, gated by explicit Actions repo variable | Optional future test for private bot messages or public channel attachments, not executed with live credentials |

Caution: prices are from publicly visible Telegram text, not newly queried Coupang live prices.
Original-price labels have not been verified. Product/photo/itemId/vendorItemId matching is not confirmed.

## Experimental code

- gh_only/pipeline.py: one public Telegram channel HTML read, strict price parser, attached/preview image candidates, labeled provenance and blockers.
- gh_only/test_pipeline.py: synthetic source fixtures and deterministic tests.
- gh_only/partner_api.py: optional approved Coupang Partners API HMAC and deeplink adapter. No live API requests in CI, no paid APICK. Coupang Wing seller API is different.
- gh_only/test_partner_api.py: HMAC and exact-original-link tests without credentials.
- gh_only/mtproto_reader.py: optional Telethon source reader using existing user authorization for attached media. No login prompts, message sending or posting.
- gh_only/test_mtproto_reader.py: fake-message tests.
- .github/workflows/github-only-prototype.yml: experimental branch push verification and one public Telegram fetch. Artifacts kept for seven days. Optional MTProto job is disabled unless enabled by repo variable.

## Independent GitHub-only future flow

Public Telegram channel or approved Telegram user session
  -> provenance-labeled deal records and optional photos
  -> exact product/option verification
  -> personal Partners deeplink generation
  -> Daangn Cafe GitHub-hosted publisher
  -> receipt confirmation
  -> durable checkpoint commit.

All latter stages remain incomplete. Nothing is published automatically from this branch.

## Constraints and blockers

1. Public Telegram latest posts expose limited images; the only two preview images in the observed eight posts were external merchant links. Need exact Coupang product photo access or source messages containing matching photos.
2. Official Coupang Partners affiliate API needs account eligibility and approved credentials. No key was read or uploaded. Product search response may not identify the exact option or original price.
3. Github-hosted runner does not preserve logins across runs. Daangn and Partners session reliability are NOT validated.
4. Another Telegram bot cannot generally use Bot API getUpdates to read private messages sent by a different bot. A user-session MTProto client, or another authorized source, is required.
5. Missing item/vendor identity, image confidence, own affiliate link, or publishing receipt must block auto-post.
6. Existing public page is a recent-post window; delays or missing polls can miss entries.
7. GitHub cron runs on the default branch; this test branch has no recurring production schedule.

## Test commands

python -m unittest -v gh_only.test_pipeline gh_only.test_partner_api gh_only.test_mtproto_reader

python -m gh_only.pipeline --mode fixture --fixture-html gh_only/fixtures/jirum_sample.html --output fixture_deal_report.json

python -m gh_only.pipeline --mode live --output live_deal_report.json

## Optional Telegram attachment test (NOT enabled)

GitHub Secrets required: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_STRING_SESSION.
Optional source variable: GH_ONLY_SOURCE_USERNAME (default jirum).
Explicit enable variable: GH_ONLY_MT_READER_TEST_ENABLED=true.

The StringSession is equivalent to an authenticated account: never paste it into chat,
a commit or logs. The gated step would upload media artifacts for three days in this private repo only when enabled.

## Next engineering checkpoints

- Verify true attached media from authorized Telegram message source.
- Obtain own affiliate link without copying someone else's tracking link.
- Check exact price and image against matching product/item/vendor IDs before posting.
- Create durable no-duplicate publish ledger and independently test Daangn publisher.
- Integrate only after a full isolated no-publish end-to-end dry run passes.
