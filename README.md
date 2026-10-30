# 딜스팟 + 네이버 카페 신규 등록 텔레그램 알림 봇

한 번의 GitHub Actions 예약 실행으로 **딜스팟과 네이버 카페를 각각 감시**합니다. PC가 꺼져 있어도 동작합니다.

## 감시 대상 및 알림 방식

| 사이트 | 감시 페이지 | 기준 상태 파일 |
| --- | --- | --- |
| 딜스팟 | https://deal.korea-inc.com/deals/ | `state/seen_products.json` |
| 네이버 카페 | https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L | `state/naver_seen_articles.json` |

- 각 사이트 **첫 페이지**만 조회합니다. 딜스팟은 상품 URL, 네이버 카페는 게시글 번호로 새로운 등록을 구분합니다.
- 가격, 할인율, 카테고리 필터는 없습니다. 네이버 카페는 공개된 글 목록(제목/번호)을 로그인 **없이** 읽으며, 공지글은 제외합니다.
- 기준 목록에 없는 항목만 텔레그램 봇으로 제목과 링크를 전송합니다. 전송에 성공한 항목은 저장하며, 한 사이트당 한 번에 최대 10건 전송합니다.
- 상태 파일이 없을 때는 현재 보이는 항목을 **기존 항목으로 저장만 하고 알림을 보내지 않습니다**.
- 사이트별 상태 파일은 **독립적**입니다. 삭제·초기화하지 마세요.
- 현재 코드는 두 사이트 모두 최신 등록이 첫 페이지에 먼저 표시된다고 가정합니다. 두 검사 사이에 한 페이지 이상 밀린 글/상품은 놓칠 수 있습니다.
- 실제 신규 등록의 텔레그램 전송은 해당 사이트에 새 항목이 나타나야 현장 검증이 가능합니다.

## 자동 실행

- 단일 워크플로: `.github/workflows/hotdeal.yml` (**DealSpot + Naver Cafe new-item alerts**)
- GitHub Actions 예약: `17,47 * * * *` (UTC 기준, 30분 간격). 실제 시작은 GitHub 사정으로 지연될 수 있습니다.
- Repository Variable: `BOT_ENABLED=true`일 때 예약 실행 활성화.
- Repository Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. 네이버 카페 감시에는 `NAVER_COOKIE`가 필요 없습니다.
- 한 번 실행할 때 두 스캐너를 별도의 단계로 실행합니다. 한쪽이 실패해도 나머지 스캔은 진행하며, 이미 보낸 메시지의 기록을 GitHub에 커밋합니다. 실패 사실은 작업 결과에 표시합니다.
- **무료 사용량:** 예약 실행 횟수가 2배로 늘지는 않지만, 합쳐진 작업의 실행 시간이 1분 단위 과금 경계를 넘으면 사용량이 증가할 수 있습니다.

## 수동 점검

1. Actions → **DealSpot + Naver Cafe new-item alerts** → **Run workflow**.
2. **preview=true** (기본값): 두 사이트를 조회만 하고 Telegram 발송 및 상태 기록을 하지 않습니다.
3. **preview=false**: 두 사이트의 신규 상품/글을 실제로 텔레그램에 보내고 상태를 저장합니다. 이미 기준 상태가 있으므로 신규 등록이 존재하면 바로 발송됩니다.
4. 로그에서 `DealSpot first-page scan`, `Naver Cafe first-page scan`, `Persist both alert histories` 순서로 확인합니다.

이전 `Naver Cafe new-post alerts (manual verification)`와 `Naver Cafe manual access probe` 워크플로는 스케줄이 없는 별도 점검용 파일이며, 자동 검사 횟수를 늘리지 않습니다.
