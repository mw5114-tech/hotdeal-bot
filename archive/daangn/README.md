# 당근카페 발행 자동화 — 보관 / 비활성

**기존 발행기: ARCHIVED. 새 카페의 독립 FIFO 스캔은 활성화, 제휴글 공개 발행은 정책 확인 전 잠금.**

- 보관된 워크플로: `archive/daangn/daangn-fifo-paused.yml` (기존 Daangn 스캔·FIFO·브라우저 발행 단계 복사본; 발행 설정은 `DAANGN_PUBLISH_PAUSED: 'true'` 유지)
- 당근 원본 상태: `state/daangn_jirum_manual_fifo.json` (원위치 보존, 네이버 자동화가 쓰지 않음)
- 당근 처리 코드: `gh_only/jirum_manual_queue.py`, `gh_only/jirum_manual_publisher.py`, `gh_only/daangn_draft.py` 및 관련 사진/조회 도구 (기존 경로에 그대로 보존)
- 공용 코드: `gh_only/jirum_manual_queue.py`의 공개 텔레그램 조회/검증 함수, `gh_only/default_deal_automation.py`의 가격·사진 처리 등은 **네이버에서도 import 중**이므로 옮기거나 삭제하면 네이버가 중단됨.
- 기존 당근 로그/README: `docs/JIRUM_MANUAL_FIFO_DAANGN.md`, `docs/DAANGN_CAFE_GITHUB_DRY_RUN.md` 참고.

## 다시 켜려면 (지금은 하지 말 것)

1. 사용자 승인 후 보관된 YAML을 **별도 이름** `.github/workflows/daangn-cafe-restored.yml`으로 복사한다. `daangn-jirum-manual-fifo.yml`은 **옛 오라클 버전용 네이버 전달 호환 파일**이므로 덮어쓰면 안 된다.
2. 별도의 당근 전용 수동 또는 예약 트리거를 구성한다. 오라클의 네이버 트리거와 공유하지 않는다.
3. 당근 로그인 세션/활동 사이트의 파트너스 허용 여부를 재확인한다. `DAANGN_PUBLISH_PAUSED`는 기본 `'true'`에서 임의로 바꾸지 않는다.
4. `state/daangn_jirum_manual_fifo.json`의 미발행·불확실 기록을 검토하고 중복 방지·게시 테스트를 통과한 뒤에만 재개한다.

## 현재 네이버 메인

- `.github/workflows/naver-blog-auto-main.yml`: 이벤트 + 30분 백업, 네이버만 스캔/발행
- `.github/workflows/daangn-jirum-manual-fifo.yml`: **파일명만 옛 이름**인 호환 전달기, 당근 발행 기능 없음
- 오라클의 기존 배포본은 호환 전달기를 통해 네이버 메인 호출 가능. `jirum_github_dispatch.py`의 최신 버전은 네이버 메인을 직접 호출하나, **GitHub 커밋만으로 오라클 서버 실행 파일이 자동 갱신되지는 않는다.**

## 복구 현황 — 2026-11-11

- 새 대상 카페: `https://cafe.daangn.com/kupang-teuggaye`.
- 복구 워크플로: `.github/workflows/daangn-cafe-restored.yml` (기존 archive 보존).
- **오라클 알림 1회**가 `naver-blog-auto-main.yml`에서 네이버와 당근을 **독립 job**으로 시작. 한쪽 실패해도 다른 쪽이 블록되지 않음. 당근 자체 백업 cron은 `:26/:56`.
- 각각의 FIFO, job concurrency lock, 쿠팡 API SUBID를 분리 (네이버 `naverblog`, 당근 `daangncafe`).
- 당근 기존 상태 `state/daangn_jirum_manual_fifo.json`의 미발행 오래된 딜은 60분 기준으로 자동 제외; 과거 발행 IDs 재게시 안 함.
- 새 카페 세션 및 글쓰기 URL은 실제 **읽기 전용** 테스트에서 정상 (HTTP 200). 게시판 명칭은 `🔥 오늘의핫딜` (띄어쓰기 없음)으로 확인해 반영.
- **쿠팡파트너스 활동 금지 사이트 관련 과거 반려 때문에 자동 공개 발행은 현재 OFF**.
  `DAANGN_PUBLISH_PAUSED=true`, `DAANGN_CAFE_PUBLISH_ENABLED=false`,
  `DAANGN_CAFE_PARTNERS_PERMISSION_CONFIRMED=false` 세 조건을 유지.
- 쿠팡파트너스에 새 카페 URL 등록 가능/활동 허용 여부를 먼저 서면 확인. 도메인이 같아 이전 반려 사유가 남아 있을 수 있음.
- 허가 확인 후에만 해당 공개발행 설정을 별도 승인에 따라 변경하고, 공식 API `shortenUrl`을 `daangncafe`로 발급해 사용. 직접 만든 `AFFSDP` 링크로 자동발행하지 않음.
- 참고: 스캔 성공은 네이버·당근 실제 게시 성공과 다름. 당근 새 카페에는 이 복구 과정에서 글을 게시하지 않았음.
