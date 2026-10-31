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

## 2026-11-01: Messaging-platform cached link preview strategy

- User showed an ordinary KakaoTalk link card for a Coupang short link displaying accurate-looking product packaging and title despite GitHub hosted Coupang product page access-denied.
- Official Kakao documentation confirms Kakao servers retrieve OG meta tags `og:image`, `og:title`, `og:description` and cache link previews. However Kakao Sharing API is designed to SEND a message and does not offer a documented public REST method that returns arbitrary URL's cached preview-photo bytes to developers. Kakao Developers' URL metadata tool can be used MANUALLY to inspect a URL's cached OG tags where eligible.
- Better official API suitable for Github server: Telegram MTProto `messages.getWebPagePreview`, a users-only API which returns `messages.webPagePreview` containing the media.webpage.photo and title/description when available. It does NOT require sending the URL to another chat and may use Telegram's independent cache/network instead of a blocked GitHub direct Coupang product-page GET.
- Implemented `gh_only/telegram_preview_probe.py` and `gh_only/test_telegram_preview_probe.py` plus gated workflow `.github/workflows/jirum-telegram-web-preview.yml` to check one source shortlink read-only, optionally download photo into a private ephemeral artifact. No user session or chat credentials are ever committed/logged; no messages sent and no publishing.
- Offline GitHub Actions run 41227374330: 6/6 unit tests PASS; live job SKIPPED because repo variable `GH_ONLY_TG_PREVIEW_TEST_ENABLED` is unset. Live requires `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_STRING_SESSION` secrets from user's OWN authorized Telegram session; no user-input passwords needed in chat.
- Photo from Telegram's preview (if present) is STILL cached/possibly stale, not an automatic proof of `itemId`, `vendorItemId` or rights to republish. A preview card is not the same thing as media attached to the source Telegram channel's post.
- Existing Kakao REST API image-search secret and explicit image search workflow are separate; do not treat either search API results or the KakaoTalk screenshot as an official Coupang product verification.

Next: manual Kakao Developers Tools > KakaoTalk URL metadata management input one known Coupang short link and inspect whether `og:image` is exposed. This UI is a manual diagnostic, not a public batch REST endpoint and may only support Kakao API shared links. For unattended GitHub-only preview capture, test the official Telegram users-only MTProto preview job once authorized. No changes to main production, Daangn posts, or recurring schedules.

## 2026-11-01 User-observed Kakao OG image exact filename and 640px download

- User manually entered original @jirum shortlink `https://link.coupang.com/a/iiCmQxWdUG` into the Kakao URL OG management tool and received `og:url=https://www.coupang.com/vp/products/6221369038`, `og:title=오뚜기 핫케이크 믹스, 1kg, 1개`, and a true original 640x640 `og:image` URL at `https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/retail/images/87060605900171-f042e02a-7905-47b9-8c3f-24f261864cf5.jpg`.
- The asset-relative filename/path is identical to our previous 492px CDN experiment, which was previously considered unverified based on the source where it was found. User's Kakao OG screenshot now demonstrates this same asset is displayed in the source shortlink's link preview for *this product*; note OG URL omits itemId and so selected option/vendor matching is still not independently proved.
- GitHub Actions run **41227876467** downloaded the screenshot-supplied **640x640 JPEG** directly from Coupang CDN: HTTP **200**, **109,232 bytes**, correct 640x640 image dimensions. Artifact **13053045208** privately stores image+report for 3 days. No Coupang product page access.
- Actual decoded image shows the front of the Ottogi hotcake mix pink/yellow 1kg package. This matches the manually observed Kakao preview style. Publication rights and actual exact itemId/vendorItemId are not confirmed.
- The crucial automation blocker is **not CDN download but unattended OG URL discovery** from each arbitrary @jirum Coupang link. Kakao developer OG metadata management is a UI diagnostic (official developer docs), not a documented general-purpose REST API for bulk arbitrary URL->og:image retrieval. Do not claim screenshot manual URL extraction is an automatic API solution.
- An official user-authenticated Telegram `messages.getWebPagePreview` method (already implemented experimental code, gated and NOT live tested) might return a webpage photo using Telegram's cache; this is next legitimate free GitHub-only experiment if user session credentials can be configured in GitHub Secrets. Cached previews can be missing/stale; no guaranteed itemId match.

Updated complete status: 1 source-link preview photo proven downloadable by GH Actions, 0 autonomous arbitrary-link picture discoveries, 0 validated personal affiliate link creations, 0 Daangn posts. Production main and schedules untouched.

## 2026-11-01: User proposal — GitHub Playwright controls signed-in Kakao Developers OG debugging UI

- Proposed pipeline: @jirum Telegram shortlink is detected -> parse original URL and productId/itemId from one safe 302 redirect -> **paste ORIGINAL shortlink** into Kakao Developers URL metadata debugger -> read `og:title`, `og:url`, `og:image` -> download public Coupang CDN image -> attach to Daangn draft. No Telegram preview API needed.
- Official Kakao Developers docs `https://developers.kakao.com/docs/ko/tool/common` describe this as a URL metadata *management/debugging UI*, not a documented public REST API for arbitrary URL OG fetching. Tool use is described for Kakao Message/Share API scrap messages or KakaoStory links. The earlier manual @jirum link lookup worked, but automatic behavior over new links is NOT established.
- GitHub-hosted Playwright no-login read-only run **41228237099**: browser reached `accounts.kakao.com/login/` after opening `https://developers.kakao.com/tool/debugger`. Thus web session required; existing desktop login and `KAKAO_REST_API_KEY` do NOT authenticate a fresh ephemeral GitHub runner.
- Implemented optional `gh_only/kakao_og_authenticated_probe.py`, `gh_only/test_kakao_og_authenticated_probe.py`, and `.github/workflows/kakao-og-authenticated.yml` to accept a separately user-managed `KAKAO_DEV_STORAGE_STATE_JSON` GitHub Secret containing previously authorized browser state. Requires explicit `GH_ONLY_KAKAO_OG_UI_TEST_ENABLED=true` GitHub Actions variable. No automated password entry, no cache reset, no session dumping into logs or artifacts, no posting. Auth session is a sensitive credential; user must never paste it into chat or a git commit.
- Offline GitHub run **41228390123**: **5/5** guard unit tests passed. Optional real authenticated test intentionally **SKIPPED** since no cookie/browser state is authorized/connected. The UI selectors are best-effort and not confirmed against a logged-in GitHub runner.
- Even if the login session is accepted and returns `og:image`, the Kakao cached screenshot `og:url` in our example omitted itemId. It proves a preview-image association with a link/productId but NOT selected seller/vendor/item option, freshness or right to republish. Publishing remains disabled.
- Stability risks: authenticated Kakao session may expire or be rejected across datacenter IPs, 2FA may require manual re-authentication, page layout changes, and use of developer-debugging UI for batch extraction might not be supported by Kakao policy. Treat as one-shot feasibility experiment, not a certified production API.

No main branch, scheduled collector, personal partner link, Daangn publisher or Muse VM modified. PR remains DRAFT.

## 2026-11-01: Kakao existing-login session debugging and successful read-only OG discovery

- Root cause of previous `ValueError`: hardcoded exact cookie-domain list rejected valid Playwright storage state containing unrelated cookies. No cookie values or domain names from the user state were logged. Safe diagnosis run 41230028294 returned `COOKIE_DOMAIN_WHITELIST_TOO_STRICT`, follow-up run 41230088100 returned `UNRELATED_COOKIE_DOMAIN_PRESENT` (coarse classification only).
- Updated `safe_session_state()` to filter in memory and import only Kakao and Daum-owned cookie and localStorage domains. Unrelated domains are never loaded into the remote browser context; `KAKAO_DEV_STORAGE_STATE_JSON` GitHub Secret was not read back, replaced, or echoed. Provider login can still expire.
- Corrected the OG debugger path from `/tool/debugger` to `/tool/debugger/sharing`, which exposes the real metadata form.
- Kakao's OG table uses tab-delimited `og:image` label and image URL in the same row. Simplified `parse_og_row()` to split on the first whitespace; unit tests cover tab and newline.
- **FULL LIVE SUCCESS**: GitHub Actions run 41230577260 restored user-authorized Kakao session, filled the @jirum original Coupang shortlink exactly once, clicked metadata lookup, and returned `OG_CDN_IMAGE_FOUND`, `submitted_requests=1`, `has_image_candidate=true`, zero posts/messages/cache reset.
- Verified run's private JSON artifact 13052429563: `cdn_image_url_candidate=https://thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/retail/images/87060605900171-f042e02a-7905-47b9-8c3f-24f261864cf5.jpg`, OG title `오뚜기 핫케이크 믹스, 1kg, 1개 - 빵 믹스/가루 | 쿠팡`.
- The 640x640 CDN URL is exactly the same one previously proven downloadable (run 41227876467). **This is the first end-to-end AUTOMATED discovery of a valid OG photo URL via Kakao UI on GitHub-only runner, after user-supplied login session**. Actual photo downloading in the same new run is not yet wired.
- These are test evidence for ONE exact link, NOT proof of recurring login stability, support for arbitrary links, correct vendor/item option, current live price or rights to republish.
- This is a user-interface debugging tool, not an official documented bulk OG-image REST API. Treat as a low-volume one-off feasibility result, do not enable production cron or auto-post based solely on the outcome.

Production main unchanged; existing separate 30-minute workflow unchanged. PR remains DRAFT.

## 2026-11-01: Fresh Coupang price retrieval test with exact itemId

- User wanted to learn whether a product page reachable from Kakao `og:url` yields a live price, and how to handle Telegram hotdeals that omit normal/original price.
- A web index returned historical archived Coupang product+item HTML for `productId=6221369038`, `itemId=12453073752`, including 5,660 KRW sale price, 5,880 KRW comparison price, 3% badge; search snapshot was approximately three months stale, NOT a live merchant quote. Opening product ID without itemId instead produced a different option (500g x 20 units, 50,600 KRW) in another archived snapshot. Never treat productId-alone price as exact product.
- **Fresh GitHub Actions one-shot direct request** (run 41231038597): requested `https://www.coupang.com/vp/products/6221369038?itemId=12453073752`, returned **HTTP 403**, no product HTML or live pricing extracted. No retries, login, proxy or bypass. Thus cached search-index access does not imply GitHub's price parser can access Coupang live.
- A separate third-party hotdeal price-history page https://hotduk.com/deal/156817 shows this sale at 3,180 KRW and historical 184-day low 3,180, average 4,266, high 4,750 KRW. This may provide independent historical benchmarks. These are NOT official list price, may describe different price/period conditions and cannot be substituted into '정상가' or imply a precise percentage discount.
- Fallback policy when @jirum Telegram post only gives a sale price: publish candidate draft with '게시글 표기 특가' only; DO NOT invent '정상가', discount rate, or compare to stale cached Coupang price. Can optionally add separate '외부 가격 이력/관측 가격' in manual review with source and as-of date, exact item option confirmation required.
- Kakao URL metadata OG title/description/image contains no guaranteed `product:price:amount`; it is a social preview, not live price API. Coupang Partners product API account approval was not obtained. Any posted price can change, so label source and date.
- New isolated diagnostic modules: `gh_only/coupang_live_price_probe.py`, `.github/workflows/coupang-direct-item-probe.yml`; only exact item target tested and no production behavior altered.

## 2026-11-01: Historical usual-price comparison test (official normal/list price unknown)

- Goal: @jirum provides the sale price, but often no reference price. Compare against third-party historical OBSERVED average, NOT the official '정상가'. Strict match needs source Coupang productId=6221369038, itemId=12453073752 and target '오뚜기 핫케이크 믹스 1kg, 1개'. No live Coupang detail scraping, no paid APICK, no posting.
- Initial three public GETs on GitHub Actions run **41231826593**: all HTTP 200. Lowchart proposed route `https://www.lowchart.com/6221369038-12453073752` actually resolved to the correct title (initial extractor did not yet parse average). Jigeumsa homepage HTTP 200 but no item lookup via GET. Hotduk known deal page `https://hotduk.com/deal/156817` returned 154-day low 3,790 / average 4,447 / high 4,750 KRW in that run, with NO exact target itemId match in visible purchase links. Historical source values can change across observations, so don't use unverified Hotduk stats as the target SKU's normal price.
- Lowchart detailed public page run **41231931029** displayed exact selected option title and both ID numbers in HTML. Visible summary: current observed 5,350 KRW, low 3,180 KRW, high 5,880 KRW, observed average **5,531 KRW**, last change shown 2026-11-01. These are for Lowchart's ENTIRE observation window (not guaranteed recent 60-day average), based on their own displayed site price record, not a live checkout total.
- Added reusable `gh_only/lowchart_price_reference.py`, `gh_only/test_lowchart_price_reference.py` and isolated workflow `.github/workflows/jirum-lowchart-average.yml`. GitHub Actions run **41232044718**: 8 offline unit tests PASS plus **LIVE ITEM MATCHING and HISTORY PARSING SUCCESS**. The target productId and itemId both appear in served HTML, and title contains correct brand/product/quantity; 5,531 KRW historical mean and 42.5% lower user-provided 3,180 KRW sale. `official_normal_price=False`; prevents false '쿠팡 정가' label. No merged production code, no publisher.
- Jigeumsa first public Chrome UI test in run 41231931029 was unable to find the URL input. One hydration-aware retry run **41232105973** ended `UI_PROBE_ERROR`, `TimeoutError`, submission false. Therefore no confirmed item-specific price result via GitHub, even though website homepage itself is accessible. Avoid retry loops.
- Correct status: Lowchart confirmed public exact product/item ROUTE + matching option title/IDs and observed historical avg; Hotduk numerical history visible but source exact itemId not verified; Jigeumsa unproven for automated lookup.
- Suggested draft excerpt: '뽐질 표기 특가 3,180원 / 로우차트 관측 평균가 5,531원 / 과거 관측 평균가 대비 약 42.5% 낮음 (조회일: 2026-11-01).' Do not label this '정가 대비 42.5% 할인' and do not assume the source observation date is the same as the price check date.
- Price history pages are third-party user-facing websites, not official documented data-export REST APIs. Validate usage policies, request frequencies and source identity before scheduling long-term extraction. No new cron or live publishing enabled.
