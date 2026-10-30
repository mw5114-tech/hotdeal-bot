# 딜스팟 + 네이버 카페 신규 등록 텔레그램 알림 봇

한 번의 GitHub Actions 예약 실행으로 **딜스팟과 네이버 카페를 각각 감시**합니다. PC가 꺼져 있어도 동작합니다.

## 감시 대상 및 알림 방식

| 사이트 | 감시 페이지 | 기준 상태 파일 |
| --- | --- | --- |
| 딜스팟 | https://deal.korea-inc.com/deals/ | `state/seen_products.json` |
| 네이버 카페 | https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L | `state/naver_seen_articles.json` |

- 각 사이트 **첫 페이지**만 조회합니다. 딜스팟은 상품 URL, 네이버 카페는 게시글 번호로 새로운 등록을 구분합니다.
- 딜스팟은 가격·할인율·카테고리 필터 없이 전체 신규 상품을 알립니다. 네이버 카페는 공개된 글 목록(제목/번호)을 로그인 **없이** 읽으며, 공지글을 제외하고 **제목 맨 앞에 정확히 `[ㅋㅍ]` 또는 `[ㅋㅍ프레시]`가 붙은 새 글만** 알립니다. `[알리]`, `[네이버]`, `[11번가]`, `[ㅋㅍ일부회원]` 등은 제외합니다.
- 기준 목록에 없는 항목만 텔레그램 봇으로 제목과 링크를 전송합니다. 전송에 성공한 항목은 저장하며, 한 사이트당 한 번에 최대 10건 전송합니다.
- 네이버 카페의 **신규 비쿠팡 글은 알림 없이 확인 기록에만 추가**하여 재검사 시 중복 처리되지 않게 합니다. 쿠팡 글은 텔레그램 전송에 성공해야만 전송 완료로 기록합니다.
- 상태 파일이 없을 때는 현재 보이는 항목을 **기존 항목으로 저장만 하고 알림을 보내지 않습니다**.
- 사이트별 상태 파일은 **독립적**입니다. 삭제·초기화하지 마세요.
- 현재 코드는 두 사이트 모두 최신 등록이 첫 페이지에 먼저 표시된다고 가정합니다. 두 검사 사이에 한 페이지 이상 밀린 글/상품은 놓칠 수 있습니다.
- 실제 신규 등록의 텔레그램 전송은 해당 사이트에 새 항목이 나타나야 현장 검증이 가능합니다.

## 텔레그램 메시지 형식 (AI 카페 자동 게시 연동용)

두 사이트 모두 상품명과 원본 링크만 담아 **동일한 텍스트 형식**으로 전송합니다.

```text
🆕 [HOTDEAL]

상품명: {상품명}

상품링크: {상품URL}
```

- 딜스팟: 새 상품의 이름과 해당 **딜스팟 상품 상세 링크**를 전송합니다.
- 네이버 카페: 제목이 `[ㅋㅍ]` 또는 `[ㅋㅍ프레시]`로 시작하는 새 글의 제목과 **카페 게시글 링크**를 전송합니다.
- 별도의 가격, 정상가, 할인율, 출처 필드는 넣지 않습니다. 단, 원본 제목 자체에 가격이나 판매처가 적혀 있으면 상품명에 포함될 수 있습니다.
- 모든 제목의 줄바꿈은 한 줄로 정리하고, 링크 미리보기는 꺼둡니다.
- 현재 구현된 기능은 **Telegram 알림 발송까지**이며, Telegram 메시지를 읽어 카페에 자동 게시하는 별도의 AI 연동은 아직 구현하지 않았습니다.

## 자동 실행

- 단일 워크플로: `.github/workflows/hotdeal.yml` (**DealSpot + Naver Cafe new-item alerts**)
- GitHub Actions 예약: `17,47 * * * *` (UTC 기준, 30분 간격). 실제 시작은 GitHub 사정으로 지연될 수 있습니다.
- Repository Variable: `BOT_ENABLED=true`일 때 예약 실행 활성화.
- Repository Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. 네이버 카페 감시에는 `NAVER_COOKIE`가 필요 없습니다.
- 한 번 실행할 때 두 스캐너를 별도의 단계로 실행합니다. 한쪽이 실패해도 나머지 스캔은 진행하며, 이미 보낸 메시지의 기록을 GitHub에 커밋합니다. 실패 사실은 작업 결과에 표시합니다.
- 읽기 전용 `preview=true` 실행은 `hotdeal-preview` 그룹으로 분리되어 정기 실행의 `hotdeal-state` 대기열을 막지 않습니다. 실제 전송 실행은 기존 기록 충돌 방지를 위해 `hotdeal-state`에서 순차 진행합니다.
- GitHub Actions 서비스 자체에서 비정상 대기 상태가 생기는 것까지 코드로 완전히 예방할 수는 없습니다.
- **무료 사용량:** 예약 실행 횟수가 2배로 늘지는 않지만, 합쳐진 작업의 실행 시간이 1분 단위 과금 경계를 넘으면 사용량이 증가할 수 있습니다.

## 수동 점검

1. Actions → **DealSpot + Naver Cafe new-item alerts** → **Run workflow**.
2. **preview=true** (기본값): 두 사이트를 조회만 하고 Telegram 발송 및 상태 기록을 하지 않습니다.
3. **preview=false**: 두 사이트의 신규 상품/글을 실제로 텔레그램에 보내고 상태를 저장합니다. 이미 기준 상태가 있으므로 신규 등록이 존재하면 바로 발송됩니다.
4. 로그에서 `DealSpot first-page scan`, `Naver Cafe first-page scan`, `Persist both alert histories` 순서로 확인합니다.

이전 `Naver Cafe new-post alerts (manual verification)`와 `Naver Cafe manual access probe` 워크플로는 스케줄이 없는 별도 점검용 파일이며, 자동 검사 횟수를 늘리지 않습니다.

## 뽐질(@jirum) 원본 텔레그램 전달 — 별도 5분 작업

- 워크플로: .github/workflows/jirum-forward.yml (Jirum original Telegram posts forwarder)
- 5분마다 공개 채널 https://t.me/s/jirum 을 확인하여 최근 메시지의 원본 ID를 읽습니다. 실제 작업 시작은 지연될 수 있습니다.
- Telegram Bot API forwardMessage(from_chat_id=@jirum)를 사용합니다. 원본 문구·링크·서식·미디어를 텔레그램 자체 전달 방식으로 전달하려는 구조이며 기존 [HOTDEAL] 형식으로 재작성하지 않습니다.
- 기존 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID Secrets를 재사용합니다. **현재 자동 실행은 안전을 위해 보류 중**입니다. 봇의 원본 채널 접근 검증이 끝난 뒤 BOT_ENABLED=true와 별도 Repository Variable `JIRUM_ENABLED=true`를 함께 설정하면 5분 예약 실행이 활성화됩니다. 수동 preview/verify-forward는 보류 중에도 실행할 수 있습니다.
- 최초 자동 실행에서는 과거 메시지 폭주를 막기 위해 가장 최신 게시글 1개만 전달하여 실제 권한을 확인합니다. 성공하면 state/jirum_last_forwarded.json에 기준 ID를 저장하고 이후에는 새 글만 전달합니다.
- 수동 검증: Actions → Jirum original Telegram posts forwarder → Run workflow → mode: verify-forward. 최신 게시물 1개를 보내되 기록은 변경하지 않습니다. preview는 전송하지 않는 읽기 전용입니다.
- 오류가 발생한 글은 성공으로 기록하지 않고 다음 실행에서 다시 시도하며, 성공 처리한 글의 기록은 별도로 저장합니다.
- 중요: 뽐질이 봇에게 메시지 접근·전달을 허용하지 않거나 보호된 콘텐츠라면 원본을 전달할 수 없습니다. HTTP 400/403 오류로 확인하며, 이 경우 메시지를 비슷하게 재작성한 뒤 원본이라고 속여 전송하지 않습니다. 실제 Telegram API 호출을 통해 최종 검증해야 합니다.
- 공개 웹에 표시되는 최근 메시지만 수집하므로 GitHub 실행 지연 또는 게시글 유입이 과도할 때는 일부를 놓칠 수 있습니다.
- 기존 딜스팟·네이버 카페 30분 자동화와 별개로 동작합니다.
