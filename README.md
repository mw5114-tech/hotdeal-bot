# 딜스팟 새 상품 알림 봇

**기존 상품은 무시하고, 새로 등록된 상품만 텔레그램으로 보내는 방식**으로 운영합니다.

- 출처: https://deal.korea-inc.com/deals/
- 할인율, 카테고리, 가격, 가격 갱신 시각 필터 없음
- 첫 실제 실행: 첫 페이지의 기존 상품을 `state/seen_products.json`에 기록만 하고 **알림 0건**
- 다음 실행부터: 전에 없던 상품 URL만 텔레그램으로 전송
- 같은 URL 재전송하지 않음. 전송 실패 시 기록하지 않고 다음 실행에서 재시도
- 한 번에 최대 10건 발송하며 나머지는 다음 실행에 이어서 전송
- 30분마다 예약 검사 (`17,47 * * * *`, UTC 기준). 실제 시작 시각은 변동될 수 있음
- 공식 쿠팡 API, 쿠팡 파트너스 링크 생성, 당근카페 자동 게시 없음

## 활성화 절차

1. `Actions → DealSpot new product alerts → Run workflow`에서 `preview=true`로 미리보기 실행. 제품 페이지 수와 오류 여부 확인.
2. **중요:** 다시 `preview=false`로 수동 실행. 이때는 기준 상태 파일을 처음 생성하는 단계라 텔레그램 알림이 **전송되지 않아야 정상**입니다. Actions에서 변경된 상태를 자동 커밋하는지 확인하세요.
3. `Settings → Secrets and variables → Actions → Variables`에서 `BOT_ENABLED=true`로 설정해 예약 실행을 활성화하세요. `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`는 Secrets에 이미 등록한 값을 사용합니다.
4. 그 뒤 새 상품이 등록되면 (최대 약 30분 + GitHub 예약 지연) 텔레그램 메시지가 옵니다.

**상태 파일을 삭제하거나 초기화하면 처음부터 기준 목록을 재생성하므로 주의하세요.** 첫 페이지에 최근 등록 상품이 먼저 나타난다는 가정으로 동작합니다. 검사 사이에 상품이 한 페이지 이상 밀리면 놓칠 수 있습니다. 상품이 0건이거나 페이지 수 제한을 초과하면 기록을 덮어쓰지 않도록 합니다.

이 봇은 상품 등록을 감지하는 용도이며, 현재가나 할인율이 사실인지 검증하지 않습니다.


## 네이버 카페 신규 글 알림 (공개 글 목록, 검증 단계)

- 카페: https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L
- 최근 글 제목/번호 목록은 로그아웃 상태에서도 열람 가능한 것으로 확인됨. **글 본문의 공개 여부와 별개**.
- `naver_new_posts.py`는 네이버 로그인, `NAVER_COOKIE` 없이 첫 페이지(최대 50건)의 게시글 메타데이터를 읽음.
- 공지 목록과 명시적 NOTICE 타입은 제외하고, 새 게시글 번호마다 제목/링크를 기존 텔레그램으로 전송하도록 구현됨.
- 별도 워크플로 `Naver Cafe new-post alerts (manual verification)`는 **수동 실행 전용**이고 기존 DealSpot 정기 실행에 영향을 주지 않음.
- 먼저 `preview=true`로 읽기/단위 테스트 확인. 이후 `preview=false`로 실행하면 `state/naver_seen_articles.json`에 최초 기준 목록을 기록하고 전송 0건.
- 새 글 전송과 스케줄 실행은 실제 라이브 검증 후 기존 DealSpot 워크플로에 결합할 예정. 별도 30분 스케줄을 추가하면 GitHub Actions 무료 사용량이 늘어남.
- 더 이상 필요하지 않은 GitHub Secret `NAVER_COOKIE`는 GitHub Settings에서 삭제 권장.
