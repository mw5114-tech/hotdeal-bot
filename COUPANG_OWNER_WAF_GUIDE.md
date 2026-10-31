# GitHub Actions에서 쿠팡 파트너스 웹 접근 허용 여부 검증 — 사이트 운영자용

이 방식은 주거용 IP를 위장하지 않는다. GitHub-hosted Ubuntu 러너는
실제 GitHub 네트워크에서 요청을 보내고, 사이트 운영자가 **본인 WAF에서
정확히 허용한 테스트 트래픽**만 식별할 수 있도록 선택적 헤더를 사용한다.

## 기존 문제

- Ubuntu/Windows/macOS GitHub-hosted 기본 브라우저: 파트너스 루트 403
- GitHub에서 Cloudflare WARP 활성화: HTTP 200이지만 HTML에는 Access Denied
- 아직 사용자 계정으로 로그인하거나 간편 링크를 생성한 성공 사례 없음

## 처음: WAF 규칙 없이도 테스트 실행 가능

1. Actions > Coupang owner WAF access check (GitHub only)
2. Run workflow
3. 브라우저 및 Python 요청의 분류 결과, 네트워크 출구 IP,
   서버 식별 응답 헤더만 확인 (쿠키/페이지 원문/비밀번호는 출력 안 함)
4. 실패 시 GitHub의 동적 출구 IP를 얻을 수 있음. GitHub 일반 러너 IP는
   새 실행 시 달라질 수 있으므로 하나의 IP만 영구 등록해서는 부족함.

## 운영자가 WAF를 직접 관리할 수 있다면 (권장)

**사이트 운영자 권한을 가진 사람이** WAF에서 다음 조건을 모두 만족하는
요청에 한하여 오탐인 보안 규칙만 예외 처리할 수 있다.

- 호스트: partners.coupang.com
- 경로: / (최초 공개 홈페이지 테스트)
- 요청 헤더: X-Coupang-Owner-Test, 값은 GitHub와 WAF 양쪽에만 공유한
  24자리 이상의 강한 임의 토큰
- 짧은 테스트 유효 기간, 로그 유지, 검증 종료 후 규칙 삭제
- 보호 전체 해제가 아니라 차단을 유발한 규칙만 예외 처리

Cloudflare WAF의 경우 Custom rule + Skip을 통해 특정 관리형 규칙을
조건부로 제외할 수 있음.
참고: https://developers.cloudflare.com/waf/custom-rules/skip/
Akamai를 사용하는 경우에는 해당 AppSec 정책의 Request Header 조건과
예외 기능으로 같은 목적을 수행할 수 있음.
참고: https://techdocs.akamai.com/application-security/reference/put-rule-condition-exception

이 저장소에서 대상의 실제 WAF 업체가 확인된 것은 아니므로
사이트의 실제 제품 및 정책 설정을 확인할 것.

GitHub 저장소 > Settings > Secrets and variables > Actions >
New repository secret > COUPANG_WAF_TEST_TOKEN 등록.

토큰은 채팅에 붙여 넣거나 README에 적지 않는다. 브라우저 테스트는
파트너스 도메인에만 토큰을 첨부하고 타사 사이트에는 첨부하지 않도록
설계했다.

## 결과 해석

- PUBLIC_PARTNERS_UI_REACHED: HTTP 2xx, 공식 도메인, 차단 문구 없음,
  실제 파트너스 화면 텍스트 확인. **공개 페이지 접근만 성공**.
- DENIED_BY_SITE: 403 또는 HTTP 200이지만 Access Denied 차단 화면.
- PUBLIC_RESPONSE_UNCONFIRMED: HTTP 2xx이나 파트너스 화면 확인 불가.
- HTTP_OR_DESTINATION_UNEXPECTED / NO_HTTP_RESPONSE: 주소/연결 문제.

이 기능은 비밀번호 입력, 로그인, 상품 링크 생성, 텔레그램·카페 발송을
하지 않는다. 첫 화면의 접근 허용이 확인되면 기존 로컬 브라우저
세션 복원 테스트와 결합할 수 있으나 로그인 성공 보장은 없다.
기존 30분 딜스팟/네이버/텔레그램 워크플로는 그대로 유지한다.

GitHub 고정 IP의 공식 경로는 GitHub Enterprise Cloud의 larger runner
일부 구성에서 지원한다. 일반 러너는 IP가 동적이라 광범위한
IP 전체를 허용목록에 넣는 것은 권하지 않는다.
https://docs.github.com/en/actions/how-tos/manage-runners/larger-runners/manage-larger-runners
