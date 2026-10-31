# 쿠팡 파트너스 로그인 상태를 클라우드에서 재사용하는 1차 실험

이 단계는 **컴퓨터 종료 후 GitHub Actions에서 기존 로그인 인증 상태가 살아있는지** 시험하는 도구다. 테스트 성공 전에는 24시간 자동 발급이 구축됐다고 판단하면 안 된다.

## 현재 상태

- 사용자 PC Chrome 로그인/간편 링크 생성: 작동 확인.
- 기존 GitHub Actions 3개 OS: 쿠팡 인증 진입에서 403 관측.
- 저장소에 수동 검증 워크플로 .github/workflows/coupang-session-reuse.yml 가 이미 있음.
- 이 PR은 로컬에서 로그인 상태를 받아 암호화해 두 GitHub Actions Secrets로 옮기는 도구를 추가함.
- 기존 30분 신규딜/카페 알림, 뽐질 전달, 텔레그램 전송은 건드리지 않음.

## Windows에서 실행

저장소를 다운로드한 폴더에서:

~~~powershell
python -m pip install playwright cryptography
python -m unittest -v test_coupang_session_export.py
python coupang_session_export.py --mode cdp
~~~

CDP 방식은 Chrome이 **이미 127.0.0.1:9222로 실행 중**이고
파트너스의 '간편 링크 만들기' 페이지가 열린 상태여야 가능하다.

Chrome 136+의 일반 기본 프로필은 원격 디버깅 포트가 제한돼 실패할 수 있다.
이 경우에는 다음 명령을 실행한다.

~~~powershell
python coupang_session_export.py --mode managed
~~~

별도 Chrome 창이 열리면 화면에서 **직접 한 번** 로그인하고
'링크 생성 > 간편 링크 만들기' 페이지에 들어간 다음 터미널에서 Enter.
생성 파일은 홈 폴더의 coupang-session-transfer/secrets/ 경로에 위치한다.
이미 생성된 파일을 덮어쓰지 않으므로 재발급하려면 기존 파일 2개를 직접 치워야 한다.

## GitHub 저장소 Secrets 등록

1. 저장소 Settings > Secrets and variables > Actions > New repository secret.
2. COUPANG_SESSION_ENVELOPE.txt 내용 → 이름 COUPANG_SESSION_ENVELOPE.
3. COUPANG_SESSION_KEY.txt 내용 → 이름 COUPANG_SESSION_KEY.
4. Actions > Coupang session reuse (dummy default; no login clicks) >
   Run workflow > mode=session 을 선택해 1회 실행.

파일이 로그인 권한을 재현할 수 있으므로 저장소에 커밋하거나 채팅에 붙이지 말 것.
해당 도구는 파트너스/쿠팡 범위 쿠키와 파트너스 로컬 스토리지만 내보내며
사용자 Chrome의 전체 프로필을 복사하지 않는다.

## 결과 해석

- MEMBER_UI_CONFIRMED: **현재 시점** GitHub에서 간편 링크 회원 UI 확인 성공.
  이후 상품 URL 입력/생성/검증 단계를 붙일 수 있다.
- HTTP_BLOCKED_STOP: GitHub 실행 IP가 거부됨. 인증 세션 복사 성공 여부와 별개.
- AUTH_REDIRECT_STOP: 세션이 거부됐거나 만료됨.
- MEMBER_AUTH_NOT_CONFIRMED_STOP: 사이트 화면 구조가 다르거나 인증이 복구되지 않음.

GitHub에서 계속 403이면 **상시 실행 클라우드 VM에서 별도 일반 Chrome을 실행해
그 VM 안에서 직접 로그인하고 영속 프로필로 작업**하는 방법을 검토한다.
로컬 Windows Chrome 프로필 폴더만 Linux/Windows VM으로 복제해서는
App-Bound Encryption 등에 의해 인증 쿠키가 그대로 이전된다고 보장할 수 없다.
VM 또한 사이트의 접속 제한을 받을 수 있다.

본 도구는 본인 화면에서 직접 로그인하며 CAPTCHA/추가 인증을 우회하지 않는다.
이 단계는 링크 자동 생성/당근카페 발행을 켜지 않으며 기존 워크플로를 보존한다.
