# 아이폰 + GitHub Actions로 쿠팡 파트너스 접속 실험 (공유기 설정 불필요)

Tailscale 공식 문서는 iOS 기기를 Exit Node(인터넷 출구)로 사용할 수 있다고 안내한다.
GitHub Actions는 tailscale/github-action@v4 로 동일 Tailnet에 접속할 수 있다.

**목표:** 사용자 PC를 끈 상태에서 GitHub Ubuntu 러너가 아이폰의 Wi-Fi/셀룰러
인터넷 연결로 쿠팡 파트너스 홈에 접속할 수 있는지 1회 시험한다.

이 코드는 홈페이지 접속 진단만 수행한다. 로그인 자동화, 상품 변환,
카페 게시, 정기 반복은 구현하거나 실행하지 않는다.

## 아이폰에서 한 번 설정

1. App Store에서 Tailscale 최신 버전 설치 (iOS exit node는 v1.98.1 이후 지원).
2. Tailscale 로그인 후 VPN 연결 허용.
3. 앱에서 Exit Node > Run as Exit Node 선택.
4. https://login.tailscale.com/admin/machines 에서 해당 iPhone 장치의
   ... > Edit route settings > Use as exit node 체크 및 저장.
5. 장치의 Tailscale IPv4 주소 100.x.x.x 기억. 이 주소는 외부 공인 IP 아님.
6. 아이폰 Tailscale을 켜 두어야 하며, iOS 절전/화면 잠금/셀룰러 변경 시
   24시간 연결 유지가 보장되지 않는다.

## GitHub Secrets 2개

GitHub 저장소 Settings > Secrets and variables > Actions > New repository secret

- TAILSCALE_AUTHKEY: Tailscale 관리자 콘솔의 Settings > Keys에서 생성한
  GitHub 전용 reusable ephemeral auth key (공개 금지).
  https://login.tailscale.com/admin/settings/keys
- TAILSCALE_IPHONE_EXIT_IP: 승인된 iPhone의 Tailscale 100.x 주소.

Tailscale 기본 access control에서 로그인된 태그/장치가 인터넷 출구를 쓸
권한이 있어야 하며, Custom ACL/Grants 사용 시 autogroup:internet 허용이 필요하다.

## 실행

GitHub Actions > Coupang GitHub via iPhone Tailscale exit node > Run workflow.

- PARTNERS_HOME_REACHED_NOT_AUTHENTICATED: 실제 2xx 홈 문서를 받음.
  로그인이 됐거나 간편 링크 생성이 된 것은 아니다.
- PARTNERS_STILL_UNREACHABLE: HTTP 403/기타 차단/브라우저 오류.
- TAILSCALE_EXIT_NODE_NOT_ONLINE: 아이폰 Exit Node 설정 또는 앱 연결 확인.

기존 Linux/Windows/macOS GitHub-hosted 러너에서 확인된 403은
네트워크에서 요청이 거부됐다는 뜻이며, 아이폰 경유 시에도 쿠팡이
다른 조건으로 차단할 수 있다.

고정 IP, 공유기 PPTP, 클라우드 VM 또는 새 장비는 필요하지 않다.
다만 아이폰이 꺼지면 이 방식도 중단된다. 일반 메인폰을
24시간 상시 라우터로 사용하는 것은 배터리와 안정성 면에서 한계가 있다.

기존 30분 딜스팟/카페 알림, 텔레그램, 뽐질 재전송은 변경하지 않는다.

공식 자료:
- https://tailscale.com/changelog (2026-05-13 iOS exit node)
- https://tailscale.com/docs/features/exit-nodes/how-to/setup?tab=ios
- https://tailscale.com/docs/integrations/github/github-action
