# ipTIME N704SR — GitHub Actions를 집 인터넷으로 접속시키는 PPTP 테스트

ipTIME 제조사 N704SR 제품 설명에 따르면 VPN 서버는 PPTP 방식만 지원한다.
그러므로 WireGuard는 이 공유기에 설정할 수 없다.

## 공유기에서 할 일

1. 공유기에 연결된 PC/휴대폰에서 http://192.168.0.1 접속.
2. 관리자 로그인 > 관리도구.
3. [VPN 설정] > [PPTP/L2TP 서버 설정] > [PPTP 서버].
   오래된 화면이면 [고급 설정] > [특수 기능] > [VPN 서버 설정].
4. PPTP 서버 사용 켜기, VPN 전용 계정 추가.
5. 되도록 PPTP 암호화(MPPE)를 켠다. 이 워크플로는 MPPE-128을 필수로 요구함.
6. 외부 IP(공인 IPv4인지 확인), ipTIME DDNS 설정 유무 확인.
7. 상위 통신사 공유기/NAT가 있으면 PPTP 포트 TCP 1723과 GRE(프로토콜 번호 47)가 통과해야 함.
8. 공유기에서 VPN 원격 사용자가 집 인터넷 회선을 통한 인터넷 연결이 가능한지 확인.

## GitHub Actions Secrets

- COUPANG_PPTP_ENDPOINT: 집 공유기 DDNS 이름 (예: home.iptime.org) 또는 외부 IPv4. 접두사 https:// 및 포트 제외.
- COUPANG_PPTP_USER: PPTP 서버에 만든 별도 계정(영문/숫자/일부 특수기호, 최대 64자).
- COUPANG_PPTP_PASSWORD: PPTP 계정의 강한 암호, 12-100자 영문/숫자 및 _!@#%+=:.,- 만 허용.

절대로 공개 채팅, 깃허브 파일, Actions logs에 VPN 비밀번호를 올리지 않는다.

GitHub Actions > Coupang N704SR PPTP from GitHub runner > Run workflow.
실제 연결은 수동 실행만 가능하며 예약 트리거가 없다.
GitHub Ubuntu 러너에서 1회 PPTP 세션 연결을 시도하고 성공하면
쿠팡 파트너스의 IPv4 목적지 주소에만 VPN 경로를 추가한다.
기본 인터넷 경로는 바꾸지 않는다.

결과:
- COUPANG_GITHUB_HOME_PPTP_WEB_ACCESS_OK: 홈 페이지 HTTP 2xx로 전달됨.
- PPTP_CONNECT_FAILED: TCP 1723/GRE 통신 차단, 공유기 WAN/외부접속 제한,
  계정 또는 MPPE 불일치, PPP 커널 지원 문제 등 가능.
- COUPANG_GITHUB_HOME_PPTP_WEB_ACCESS_FAILED: VPN은 연결돼도
  쿠팡에서 여전히 차단했거나 페이지 로딩이 불완전함.

**주의:** PPTP는 오래되고 보안 수준이 낮다. 이 코드는 연결 범위 테스트용이다.
공유기/환경에 따라 GitHub-hosted runner에서 GRE 사용이 불가능할 수 있다.
외부 네트워크에서 실제 동작 여부를 아직 확인하지 못했다.
이 테스트가 성공해도 쿠팡 파트너스 로그인 상태 복원이나 자동 링크 생성,
당근카페 업로드까지 구현·검증됐다는 의미는 아니다.

기존 딜스팟/네이버카페 30분 알림, 텔레그램 봇, 뽐질 재전송
관련 파일과 워크플로는 건드리지 않았다.

제조사 참고:
https://www.iptime.com/products/855?pd=1
https://www.iptime.com/support/faq/25911
https://iptime.com/support/faq/27730
