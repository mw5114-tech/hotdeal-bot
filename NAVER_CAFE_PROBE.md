# 네이버 카페 수동 조회 검증

카페 `24651941`의 전체 글 목록 첫 페이지를 읽는 실험용 테스트입니다.
기존 딜스팟 코드·워크플로·상태 파일은 변경하지 않습니다.
`naver-cafe-probe.yml`은 **workflow_dispatch만** 지원하며 예약, push, PR 트리거가 없습니다.
텔레그램 전송, 게시글 저장, 로그인 자동화, CAPTCHA·추가 인증 우회가 없습니다.

## 1. 크롬에서 인증 쿠키 확인

1. 카페에 가입한 계정으로 네이버에 직접 로그인합니다. CAPTCHA나 추가 인증은 사용자가 정상 절차로 완료합니다.
2. https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L 에서 전체 글 목록이 실제로 보이는지 확인합니다.
3. `F12` 또는 `Ctrl+Shift+I` → **Application** 탭을 엽니다. 탭이 숨겨졌으면 `>>`에서 찾습니다.
4. 왼쪽 **Storage → Cookies → https://cafe.naver.com**을 선택합니다. 보이지 않으면 로그인한 `https://www.naver.com` 페이지에서 같은 메뉴를 확인합니다.
5. 이름이 정확히 `NID_AUT`, `NID_SES`인 쿠키의 **Value** 원문을 각각 복사합니다. 일반적으로 Domain은 `.naver.com`입니다. `Show URL-decoded`는 끄고 값의 `%`, `=`, 대소문자를 바꾸지 않습니다.
6. 아래 형식으로 한 줄을 만듭니다. `Cookie:` 접두어, 따옴표, 줄바꿈, Domain/Path/Expires 같은 속성은 넣지 않습니다.

```text
NID_AUT=실제_NID_AUT_값; NID_SES=실제_NID_SES_값
```

두 값이 없으면 같은 계정의 로그인 상태를 직접 확인하세요. 쿠키 값이나 쿠키 화면은 채팅·이슈·스크린샷·저장소 파일로 공유하지 마세요. 콘솔에 외부 코드를 붙여 넣거나 HAR/Copy as cURL 전체를 공유할 필요가 없습니다.

## 2. GitHub Secret 등록

1. https://github.com/mw5114-tech/hotdeal-bot/settings/secrets/actions 로 이동합니다.
2. **Secrets → New repository secret**을 선택합니다. **Variables가 아닙니다.**
3. Name: `NAVER_COOKIE`
4. Secret: 위에서 만든 실제 쿠키 한 줄
5. **Add secret**으로 저장합니다. 등록 후 클립보드와 임시 메모에 남은 값을 지웁니다.

쿠키는 로그인 권한을 가진 비밀정보입니다. 만료·로그아웃·보안정책 변경으로 무효화될 수 있습니다. 유출이 의심되면 네이버에서 해당 세션을 종료하고 Secret을 교체하세요. 실행 로그를 디버깅하려고 Cookie, 응답 원문, 환경변수를 출력하지 마세요.

## 3. 수동 실행

1. **Actions → Naver Cafe manual access probe → Run workflow**
2. 검증 코드가 추가된 `main` 브랜치를 선택하고 실행합니다.
3. `Read-only authenticated access probe` 단계의 고정 진단 코드를 확인합니다.

로그에는 글 제목·회원정보·응답 본문을 출력하지 않고 결과와 글 개수만 남깁니다. 쿠키는 고정된 `apis.naver.com` HTTPS 목록 주소에만 전송하며 모든 리다이렉트를 차단합니다. 인증 요청 1회 성공 후 비인증 대조 요청 1회만 수행합니다.

| 결과 | 의미 및 다음 단계 |
|---|---|
| `PASS: AUTHENTICATED_LIST_READABLE` | 쿠키 요청에서 유효한 목록을 읽었고 비인증 대조 요청은 로그인/회원 권한 요구로 실패했습니다. 인증된 첫 페이지 접근의 근거입니다. |
| `LIST_READABLE_WITHOUT_AUTH` | 비인증 상태에서도 목록을 읽었습니다. 쿠키 인증·회원 전용 접근이 증명되지 않았습니다. |
| `AUTH_LIST_READABLE_CONTROL_FAILED` | 쿠키 요청은 목록을 읽었지만 대조 요청이 네트워크·차단 등으로 실패해 인증 여부는 미확정입니다. |
| `COOKIE_*` | Secret 누락 또는 형식 오류입니다. 위 두 쿠키만 다시 등록합니다. |
| `LOGIN_REQUIRED`, `HTTP_AUTH_REQUIRED` | 로그인 필요 응답입니다. 세션 만료·잘못된 쿠키 등을 정상 브라우저에서 확인합니다. |
| `MEMBERSHIP_OR_PERMISSION_REQUIRED` | 응답에서 회원/등급/권한 요구를 확인했습니다. 가입 계정과 게시판 권한을 확인합니다. |
| `HTTP_FORBIDDEN` | HTTP 403입니다. 회원 권한, 네이버 보안정책, 실행 환경 차단 중 무엇인지 이 코드만으로 확정할 수 없습니다. |
| `CHALLENGE_STOPPED`, `REDIRECT_STOPPED` | 추가 인증/CAPTCHA 또는 리다이렉트가 감지되어 중단했습니다. 우회하거나 자동 재시도하지 않습니다. |
| `NETWORK_OR_DNS_ERROR`, `HTTP_RATE_LIMITED`, `HTTP_ERROR` | 네트워크/DNS, 요청 제한 또는 기타 HTTP 오류입니다. |
| `*SCHEMA_CHANGED`, `NON_JSON_OR_SCHEMA_CHANGED`, `RESPONSE_ENCODING_UNSUPPORTED`, `RESPONSE_TOO_LARGE`, `EMPTY_LIST_INCONCLUSIVE` | 응답을 안전하게 유효한 목록으로 판정할 수 없습니다. 빈 목록이나 HTML 앱 화면을 성공으로 취급하지 않습니다. |

종료 코드: 성공 `0`, 조회/설정 실패 `2`, 인증 검증 불충분 `3`.
`2`, `3`은 Actions에서 실패로 표시됩니다. 이때 자동 감시를 활성화하지 마세요.

## 검증 범위와 한계

- 웹용 `ArticleListV2dot1.json` 주소와 예상 JSON 구조를 시험합니다. 공식적으로 보장된 공개 조회 API가 아니므로 실제 응답과 다르면 파서 수정이 필요합니다.
- 대상은 전체 글 메뉴(`search.menuid=0`) 첫 페이지, 최대 20건입니다. 전체 과거 글 수집이나 모든 개별 게시판의 권한 검증은 하지 않습니다.
- 별도 noticeList는 검사 대상에서 제외되지만 실제 공지 혼합 형태·게시판 누락·페이지네이션은 아직 검증하지 않았습니다. 신규 글 감시/공지 제외 구현 완료를 의미하지 않습니다.
- 테스트 데이터로 오류 처리와 인증 대조 로직을 검증한 것과 실제 네이버 세션으로 조회한 것은 구분해야 합니다. 로컬 단위 테스트 통과만으로 실제 접근 성공을 주장할 수 없습니다.

참고: [Chrome 쿠키 확인](https://developer.chrome.com/docs/devtools/application/cookies), [GitHub Actions Secrets 등록](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

