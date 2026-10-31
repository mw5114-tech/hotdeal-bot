# Decodo 한국 주거용 프록시 → GitHub 연결

## 검증된 사실 (2026-11-01 KST)

- GitHub Actions에서 공식 Decodo 게이트웨이 `gate.decodo.com:7000` DNS 조회 성공
- 같은 GitHub 러너에서 TCP 7000 포트 연결 성공
- 현재 공급업체의 계정·프록시 인증·한국 출구 IP는 **아직 검증되지 않음**
- 쿠팡 파트너스 HTTP 200 정상 화면 및 로그인은 **아직 미성공**
- WARP, 일반 GitHub 러너, ARM64, ubuntu-slim 등은 이전에 403 또는 차단 화면 확인

## 가입·체험 계정 준비 (사용자만 수행)

1. https://decodo.com/proxies/residential-proxies/pricing 에서 주거용 프록시 가입/체험 여부 확인
2. 3일 100MB 체험이 끝나면 선택한 유료 플랜이 자동 시작될 수 있으므로 자동 갱신 조건 확인
3. Residential Proxies > Proxy setup:
   - **HTTP(S) 프록시** 선택 (Chromium SOCKS5 사용자명/비밀번호 인증은 지원 제약이 있음)
   - **한국 (KR)** 위치 지정
   - **Sticky Session** 설정, 최초 테스트는 30~60분 등 동일 출구가 유지되는 세션 권장
   - **사용자명·비밀번호 인증** 사용 (GitHub 기본 출구 IP는 변동될 수 있어 IP 허용목록 방식보다 적합)
4. 업체가 설정한 대상 사이트 제한 정책 확인; 필요한 경우 공급업체에 `partners.coupang.com` 테스트가 허용되는지 먼저 문의

사용자가 만드는 업체 계정이나 결제는 GitHub 코드로 대신 실행할 수 없음.

## 이미 구현된 GitHub 연동

저장소 `Settings > Secrets and variables > Actions > New repository secret`에:

| Secret | 넣을 값 |
| --- | --- |
| `EGRESS_PROXY_SERVER` | 대시보드에서 표시된 `http://호스트:포트` (일반 residential gateway는 `http://gate.decodo.com:7000`) |
| `EGRESS_PROXY_USER` | 대시보드가 생성한 **KR + Sticky** 설정의 프록시 사용자명 |
| `EGRESS_PROXY_PASSWORD` | 업체 프록시 인증 비밀번호 |

주의: 브라우저 로그인 코드, 쿠팡 계정 비밀번호, 프록시 비밀번호를 GitHub 소스 코드나 채팅에 넣지 않는다.

실행:
`Actions > GitHub independent proxy egress verification > Run workflow`

기존 `coupang_independent_proxy_probe.py`는:
1. GitHub 기본 출구 IP 측정
2. Chromium으로 인증된 프록시 경유 공개 IP 측정
3. 두 출구 IP가 다른 경우에만 **쿠팡 공개 페이지에 1회 접속**
4. `403` 또는 `200 Access Denied`는 명시적으로 실패 분류

결과: `independent-proxy-egress-observation` artifact.
기존 텔레그램·네이버·딜스팟 정기 자동화는 영향 없음.

## 공식 문서
- 한국 위치/세션 옵션: https://decodo.com/decodo-quick-start-guide/residential-proxies
- 대시보드 인증/게이트웨이: https://decodo.com/faq/getting-started/authorization-process
- 단일 게이트웨이 `gate.decodo.com:7000`: https://decodo.com/faq/getting-started/what-are-backconnect-proxies
- 체험 자동 전환 주의: https://decodo.com/faq/billing/do-you-offer-a-free-trial
- 공급업체 대상 사이트 제한: https://decodo.com/faq/general/do-you-have-any-blocked-sites
