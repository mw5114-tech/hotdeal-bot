# GitHub Actions에서 PC를 끈 채 쿠팡 파트너스 접속 실험

## 실측

GitHub-hosted Ubuntu (Chromium), Windows (Google Chrome), macOS (Chromium)에서
Python HTTPS와 브라우저 접속 모두 HTTP 403 Access Denied.
DNS 조회는 성공. 따라서 기존 쿠팡 로그인 유무와 무관하게 입구에서 차단됨.

## GitHub 실행 + 집 공유기의 WireGuard VPN

사용자 PC는 꺼져 있어도 된다. **GitHub Actions가 자동화 엔진**이고,
집 공유기는 GitHub의 쿠팡 파트너스 접속에만 인터넷 출구를 제공한다.
다른 클라우드 서버/프록시 제공업체는 사용하지 않는다.

다만 공유기는 WireGuard VPN 서버, 인터넷 출구 NAT, 외부 원격 연결을
지원해야 한다. 공유기 모델, 회선의 CGNAT 여부에 따라 설정 자체가
불가능할 수 있다. 쿠팡 측의 다른 탐지에 의해 여전히 403이 날 수도 있다.

## 준비할 것

- 집 공유기의 WireGuard 서버 구성 + 공인 IP/DDNS + UDP 포트
- GitHub 전용 WireGuard 피어 구성, 인터넷 출구 NAT/포워딩 허용
- GitHub 저장소 Settings > Secrets and variables > Actions 아래 네 가지 Secrets:
  1. COUPANG_WG_PRIVATE_KEY: GitHub 피어 개인키(Base64 인코딩 32바이트)
  2. COUPANG_WG_ADDRESS: GitHub 피어 주소(예: 10.77.0.2/32)
  3. COUPANG_WG_PEER_PUBLIC_KEY: 집 공유기의 WireGuard 공개키
  4. COUPANG_WG_ENDPOINT: 공유기 주소:포트(예: home.example.net:51820)

## GitHub에서 실행

Actions > Coupang GitHub runner via home router WireGuard > Run workflow

GitHub Ubuntu 러너에 WireGuard를 올리되 기본 인터넷 경로를 바꾸지 않고,
DNS로 조회한 partners.coupang.com IPv4 목적지 주소(/32)만 VPN으로 라우팅.
따라서 GitHub 자체 연결은 원래 경로를 사용한다.
파트너스 홈의 Python 및 Chromium 요청만 일회성으로 검사한다.
로그인·링크 발급·텔레그램 알림은 시도하지 않는다.

- 공유기 NAT가 빠져 있으면 VPN 연결이 되어도 웹 요청은 실패함.
- 이후 로그인 과정에서 다른 도메인이 등장하면 그 도메인용 라우팅을
  별도 추가해야 한다.
- VPN 개인키나 브라우저 쿠키 파일을 Git에 커밋하지 말 것.
- 수동 워크플로만 실행; 기존 30분 알림과 텔레그램은 그대로 유지.
