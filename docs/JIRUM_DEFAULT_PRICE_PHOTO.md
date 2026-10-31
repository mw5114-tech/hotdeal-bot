# Default Jirum Telegram deal photo + observed price reference

Status: experimental branch only, manual one-shot GitHub workflow, no auto posting.
Modules: `gh_only/default_deal_format.py`, `gh_only/default_deal_automation.py`.
Workflow: `.github/workflows/jirum-default-photo-price.yml`.

## Default short copy

```text
📦 상품명 : 휘카레 셰프 오리지널 소스 순한맛, 230g, 20개
💸 쿠팡 평소 판매가(로우차트 관측 평균) : 약 34,575원
🔥 핫딜 특가 : 12,050원 (상세페이지 참고 / 약 65% 혜택)
```

- Telegram source sale is extracted from the *message text*, not from the Coupang URL alone.
- Percentage is computed against the **Lowchart observed historical mean**, then rounded to the nearest 5 percentage points (half-up), e.g. 65.1% -> 65%; 42.5% -> 45%. A 0% or 100% claim is suppressed.
- Never label the historical mean 'normal price' or 'official list price'. Original/normal price printed by the Telegram poster is deliberately not used for this calculation.
- With missing observed average or mismatched product option, line 2 states `확인 중` and line 3 shows the Telegram special price without any invented benefit percentage.
- Image candidate comes from Kakao Developers' authenticated sharing OG lookup and is downloaded from a strict Coupang CDN allowlist.
- ProductId/itemId from the first HTTP 302 redirect (no Coupang detail-page request), Kakao og:url productId/vendorItemId, Lowchart IDs, title and package size/count must agree before historical data is used.
- No affiliate link creation, no Daangn posting, no cache reset, no saved browser session in artifacts.

## Running and limits

- GitHub Actions workflow can run via `workflow_dispatch` on `test/github-only-pipeline-20261031`. Also runs on changes to its own YAML on this branch.
- Current default: inspect the last 8 public @jirum posts, automatically process the **latest eligible Coupang item only** per invocation; one Kakao metadata lookup; produce `captions.txt`, `drafts.json` and optionally a product JPG.
- **No cron** and no cross-run deduplication yet. This is not a continuously deployed bot. To enable regular checks later, first design a dedup state and confirm continued automated use is supported by Kakao's debugger.
- Production `main`, pre-existing 30-minute DealSpot/Naver workflows, Telegram forwarding and Daangn publishing are unchanged.

## Confirmed end-to-end test

- Run: https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41233724394
- 21 offline tests passed. Public Telegram message `https://t.me/jirum/94717` read in GitHub; sale 12,050 KRW.
- ProductId 9217210892, itemId 27232840915, vendorItemId 94199974724 matched.
- Kakao OG photo saved as 492x492 JPEG, `jirum_94717_kakao_og.jpg`.
- Lowchart observed historical average 34,575 KRW for matching product+item option. The reference comparison gives `약 65% 혜택`; the format now clearly labels the source as Lowchart, not an official Coupang normal price.
- Private GitHub artifact 13054652606. Posts published: 0.
