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

## 2026-10-31 Jirum-only Daangn draft iteration

- The new draft builder ingests ONLY public @jirum Telegram channel messages, not DealSpot/Naver sources. Does not log in or post to Daangn.
- gh_only/daangn_draft.py composes a neutral title, Telegram-post sale/normal prices and calculated discount. All article text excludes the original author's Coupang affiliate URL, and includes a placeholder for the user's OWN unverified affiliate link.
- Sample screenshot: Ottogi hotcake mix; sale 3,180 KRW, normal 6,235, discount AMOUNT 3,055, reduction 49.0%. Price arithmetic checks passed. The discount amount must never be interpreted as the sale price.
- GitHub Actions run 41208493211: 44 offline tests PASS, 8 latest public messages processed, 6 Coupang-only draft articles, 6 source-price candidates, 0 image candidates matching Coupang deals, 0 own affiliate URLs, 0 published posts.
- Github Actions one-hop run 41208692250: public shortlink https://link.coupang.com/a/iiCmQxWdUG returned HTTP 302, destination www.coupang.com, productId 6221369038 and itemId 12453073752. No product-page HTML request. URL-level ID candidates do NOT prove exact option product rendering or live price.
- GitHub Actions run 41208675520: all 49 offline contract tests PASS, real one-fetch public source and draft generation PASS.

New modules: gh_only/daangn_draft.py, gh_only/test_daangn_draft.py, gh_only/shortlink_probe.py, gh_only/test_shortlink_probe.py, .github/workflows/jirum-link-onehop.yml.

Remaining blockers: no confirmed corresponding Coupang item photo from latest messages; live merchant price not independently verified; personal affiliate link creation requires own approved Partners account/authentication; Daangn login/publishing not tested; de-duplication and receipt ledger not implemented.

Do not use unrelated photos from the channel Media gallery: those images are not linked to the shown post. Do not reuse original @jirum partner tracking links. No new scheduled posting is enabled.

## 2026-10-31 Image-source research and live GitHub tests

- **Source finding:** web search indexed the EXACT Coupang productId 6221369038 and itemId 12453073752 as Ottogi Hotcake Mix 1kg x1. This establishes option description from a previously crawled listing, not a live Coupang quote or reusable image license.
- **Alternative-image caveat:** publicly indexed image URL from another Coupang listing depicts the same-looking Ottogi 1kg packaging, but is NOT verified to be the exact `itemId=12453073752` listing photo. Do not auto-post it as a confirmed match.
- **GitHub indexed image search, exact ID:** Actions run 41209523593 succeeded as a job; Bing returned HTTP 200 but **zero parseable image cards**. This is not photo retrieval success.
- **GitHub CDN single image GET:** Actions run 41209723121 returned HTTP **200 image/jpeg**, valid JPEG magic bytes, **74,713 bytes**, SHA256 03bc55ea4c7178c991008e58c87bf1f97bf90bbcffbcadf024732a84bcf70b64. File preserved in private test artifact for three days. This proves GitHub-hosted runner CAN download public Coupang CDN image bytes if URL is already known.
- **GitHub indexed image search, relaxed product name:** Actions run 41209858666 returned HTTP 200, **zero image result cards**, 0 `murl` and `iusc` markers, no explicit CAPTCHA marker. Therefore this Bing page is not an automation-ready discovery source in the tested configuration.
- **Offline contract suite:** Actions run 41209846875 **59/59 tests PASS**. No image was automatically mapped to exact itemId; no partner deeplink minted; zero Daangn posts.

New modules gh_only/indexed_image_probe.py (+ tests) and gh_only/cdn_image_probe.py (+ tests); separate single-shot Actions diagnostic workflows. All writes remain in experimental branch, no schedules enabled.

### Resulting technical decision

Photo transport is SOLVED for a *known* public image URL; automatic image URL discovery and exact-option confirmation are UNSOLVED. Do not extrapolate from the CDN test to stable sourcing of every hotdeal image. Future preferred paths: source-attached Telegram media when available; approved personal Coupang Partners product data/search API if eligible, validating productId+itemId+vendorItemId and image asset license; otherwise strictly mark alternative listing images unverified and do not autopublish. Avoid arbitrary lookalikes and avoid scraping Coupang blocked pages.

## 2026-10-31 Photo discovery investigation — no Coupang approval or photo in @jirum

Confirmed user constraints: Coupang Partners final approval NOT yet obtained; source @jirum text posts do NOT attach product photos; GitHub-hosted Coupang product-page browser requests have shown 403/access-denied. We can parse productId/itemId from an original link one-hop 302 without fetching product-page HTML.

### Discovery options tested

- Existing Coupang CDN address CAN be fetched by GitHub (run 41209723121, HTTP 200, JPEG 74,713 bytes); image discovered on another product listing and NOT verified for source itemId.
- Bing exact and relaxed image index HTML runs 41209523593 / 41209858666: HTTP 200, zero parseable image cards. Not a dependable address finder.
- Google public indexed images run 41210728078: HTTP 200, 0 Coupang CDN URLs observed, no explicit CAPTCHA markers. Not viable as tested.
- Same Coupang productId can have multiple itemIds and photos; itemId is a catalog/item identifier, not the hashed Coupang CDN filename. There is no verified formula mapping those two numbers to a publicly exposed picture URL.

### New best free candidate: Kakao/Daum image search REST API

- Official API https://dapi.kakao.com/v2/search/image, app-scoped Kakao REST API key, published free DAILY IMAGE search quota 30,000. No Coupang Partners approval required. This is NOT paid APICK.
- API response includes original image URL, thumbnail, width, height, and original source doc_url, unlike HTML scraping where image cards were missing.
- Implemented gh_only/kakao_image_search.py: two max queries per test, exact IDs and descriptive product name; only image candidates whose doc_url literally contains source Coupang productId AND itemId are marked exact-URL source match; this still NEVER proves the image content is correct or reuse rights are granted.
- Workflow .github/workflows/jirum-kakao-image.yml has read-only offline tests and gated real query. Secret KAKAO_REST_API_KEY and repository variable GH_ONLY_KAKAO_PHOTO_TEST_ENABLED=true are required before a live query. No credentials exist in code, no automatic live run was performed.
- Kakao isolated offline run 41210581016: 10/10 tests PASS; optional live search intentionally SKIPPED due missing opt-in variable. Main prototype run 41210551066: total 69/69 offline unit tests PASS.
- Naver legacy Shopping Search API officially terminated 2026-07-31, not suitable as a new free integration. Google Custom Search JSON API closed to new customers, not proposed.

Production main, 30-min scheduler, Muse VM, affiliate link generation and Daangn posting all unchanged. Future step needs the USER to create a Kakao Developers REST API key and enter it directly into GitHub Actions repository Secrets, never in chat, then set the test variable. No schedule or posting will be enabled until results verified.
