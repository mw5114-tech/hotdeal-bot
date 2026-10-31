# 실제 GitHub 세션 복원 시험

- 실행: https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41239608848
- 사용자 암호화 파일 복호화 및 GitHub Secret 등록 성공 확인.
- 화면 재현 및 세션 필터 검사 성공.
- Ubuntu GitHub-hosted runner + 기본 Playwright Chromium에서 실제 세션 복원 시도.
- 결과: ACCESS_DENIED_EVEN_WITH_SESSION, HTTP 403, host partners.coupang.com.
- expected_route=true, input_field_visible=false, generate_button_visible=false.
- 링크 생성/게시: 0건.
- 쿠키 값, 비밀번호, 로그인 세션 원문은 기록하지 않음.

## 해석

이 실행 환경에서는 세션을 공급해도 사이트가 요청을 거절했다. 이것만으로 세션 자체의 유효성/만료 또는 차단 원인을 단정할 수 없다. HTTP 200이나 UI 도달 성공은 관측되지 않았다.

## 다음 경로

1. 쿠팡파트너스 공식 API 사용 자격 및 키 확보 후 공식 딥링크 API를 GitHub에서 호출.
2. 웹 UI가 꼭 필요하면 쿠팡 측에 허용되는 서버 자동화 접근 방법 확인.
3. 현재 로그인되는 사용자 PC 실행은 별도 선택지이나, PC가 꺼져도 GitHub만으로 운영한다는 요구를 충족하지 않는다.

차단을 숨기거나 무한 재시도하지 않는다. 현재 자동 게시 활성화 불가.
