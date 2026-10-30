# 이 가격 못 참지! — 딜스팟 할인 알림 봇

PC를 켜둘 필요 없이 **GitHub Actions**에서 3시간마다 딜스팟(운영자 허가를 받은 소스) 상품을 확인하고, 조건에 맞는 후보를 텔레그램으로 전송합니다.

## 현재 상태

- Python 3.11 / 표준 라이브러리, `scanner.py`
- 조건: 30일 평균 추적가 대비 최소 20% 하락, 100,000원 이하, 48시간 이내 수집, 30일 추적 데이터 최소 3회
- 대상: 대학생·주부 관심 생활용품 키워드
- 회당 최대 5건, 이미 전송한 가격보다 더 내려간 경우에만 재알림
- 실행 주기: UTC 00:17, 03:17, ... (한국시간 09:17, 12:17, ...), GitHub 일정에 따라 지연될 수 있음
- **자동 전송은 아직 OFF**: 저장소 변수 `BOT_ENABLED=true`를 추가한 후에만 정기 전송
- **당근카페 자동 게시, 쿠팡 파트너스 링크 생성은 구현되지 않음**
- 이 가격은 딜스팟 마지막 수집값이므로 구매 전 확인해야 함

## 시작하기 (소유자만)

1. 저장소 **Settings → Secrets and variables → Actions → Secrets → New repository secret** 에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 각각 추가. 코드/이슈/채팅에 토큰 붙여넣지 말 것.
2. **Actions → Hotdeal bot (DealSpot) → Run workflow** 를 열어 `preview=true` 상태로 먼저 실행. 실행 로그에서 사이트 데이터 추출을 검증.
3. 미리보기 성공 시 같은 메뉴에서 `preview=false`로 실행해 실제 텔레그램 전송 확인.
4. **Settings → Secrets and variables → Actions → Variables → New repository variable** 에 `BOT_ENABLED`=`true` 등록하면 정기 실행 활성화.
5. Actions 로그의 성공 여부와 텔레그램 도착 확인. 중복 방지 이력은 `state/sent.json`에 저장됨.

## 제한

딜스팟 사이트 구조, 수집 날짜, 제공 항목에 따라 후보가 0건일 수 있습니다. GitHub 예약 실행은 정확한 시간/무중단 운영을 보장하지 않습니다. 시크릿 없이 실전 모드는 실패하며, 그 전까지는 미리보기만 사용하세요.

자동 실행을 중지하려면 `BOT_ENABLED` 값을 `false`로 변경하세요.
