# 뽐질 원문 그대로 전송 — 현재 운영 방식

`watcher.py`가 `raw_runtime.py`, `raw_message.py`, `raw_delivery.py`를 사용합니다.
`jirum_live.py`와 `jirum_forward.py`의 예전 상품명/HOTDEAL 포맷은 이 운영 서비스에서 사용하지 않습니다.

- Telethon `NewMessage`로 `@jirum`만 수신합니다. 수정 이벤트와 재연결 catch-up을 지원합니다.
- `message.raw_text`를 strip/요약/HTML 변환/상품명 추출 없이 Bot API `sendMessage.text`로 전달합니다. 추가 문구는 없습니다.
- Telegram의 UTF-16 offset/length를 복사해 숨겨진 하이퍼링크 및 지원되는 서식 엔티티를 전달합니다. 지원하지 않는 엔티티 메타데이터는 제외해도 텍스트는 바꾸지 않습니다.
- 사진·동영상·파일은 전송하지 않습니다. 캡션이 있으면 캡션 텍스트만 전송합니다. 텍스트가 없는 미디어 글은 건너뜁니다.
- 구매 URL이 없는 텍스트도 전달합니다. 외부 URL을 방문하거나 추적 링크를 변경하지 않습니다.
- 개인방과 `핫딜 MUSE용` 그룹으로 동시에 전송하며, 대상별 Bot API 성공 영수증을 SQLite에 각각 기록합니다. 성공 응답의 텍스트가 원문과 같은지도 검사합니다.
- 같은 게시글 ID의 성공한 대상은 다시 보내지 않습니다. 최초 실행은 기준선만 만들고 과거 글을 대량 전송하지 않습니다.
- 이미 전송한 글의 수정은 재발송하지 않습니다. 한 대상에라도 시도한 이후에는 두 대상 내용 일치를 위해 페이로드를 고정합니다.
- 연결 장애 시 Telethon 자동 재연결과 해당 채널의 MTProto 기록으로 누락을 보완합니다. 주기적인 웹페이지 수집이나 GitHub Actions를 감지 수단으로 사용하지 않습니다.
- Bot API 결과가 유실된 타임아웃/프로세스 중단은 `uncertain`으로 남겨 무조건 재전송하지 않습니다. 명시적 429와 연결 전 DNS/접속 거부는 재시도합니다. 절대적인 exactly-once는 Bot API 응답 유실 상황에서 보장할 수 없습니다.
- 원문이 Bot API 한 메시지 제한(4,096자)을 초과하면 임의로 자르거나 나누지 않고 `blocked`로 남깁니다.

## Windows

설치 위치는 `C:\Users\User\hotdeal-live`입니다. `watcher.py`와 기존 `hotdeal_session.session`을 재사용했습니다.
기존 파일은 `watcher.original.private.bak`으로 보관하며, `.env`, 세션, 백업의 Windows ACL을 사용자/SYSTEM으로 제한합니다.
인증값은 로컬 `configure_gui.py`의 숨김 입력창에서 직접 입력합니다. 실제 `.env`, 세션과 SQLite는 Git에 올리지 않습니다.

클라우드로 같은 세션을 이전한 뒤에는 Windows watcher를 동시에 실행하지 마세요. PC의 설정은 자동 전송 false로 남겨 두며, 24시간 운영은 서버가 담당합니다.

## 서버

- Oracle 홈 리전: Osaka (`ap-osaka-1`), Ubuntu 24.04, `VM.Standard.E2.1.Micro` 무료 대상.
- 서비스: `hotdeal-live.service`, 전용 사용자: `hotdeal-live`.
- 코드: `/opt/hotdeal-live`.
- 비밀 설정: `/etc/hotdeal-live.env` (root, 0600).
- 세션/SQLite: `/var/lib/hotdeal-live` (서비스 사용자, 0700).
- SSH 호스트키는 Oracle 인증 콘솔의 부팅 로그와 대조해 고정했습니다. SSH 22번은 관리 PC의 공인 IP 한 곳으로 제한하며 웹 포트는 열지 않습니다.
- root 소유 `EnvironmentFile`로 인증값을 주입하고, 일반 사용자 권한으로 프로그램을 실행합니다.
- `systemd`가 부팅 시 시작하고 오류 종료 시 10초 뒤 재시작합니다. 읽기 전용 시스템 영역과 제한된 쓰기 경로를 사용합니다.

```bash
sudo systemctl status hotdeal-live --no-pager
sudo journalctl -u hotdeal-live -n 30 --no-pager
sudo bash /opt/hotdeal-live/deploy/run.sh status --database live
```

`uncertain`/`blocked`는 해당 채팅의 실제 도착 여부와 봇 권한을 확인한 뒤, 서비스를 중지하고 해당 대상만 해결합니다. `retry`를 선택하면 그 대상에 다시 보내므로 실제 미도착 여부를 먼저 확인합니다.

```bash
sudo systemctl stop hotdeal-live
sudo bash /opt/hotdeal-live/deploy/run.sh resolve --database live --post POST_ID --destination personal --part 0 --outcome sent
sudo systemctl start hotdeal-live
```

수동 `verify`는 운영 DB와 다른 테스트 DB에 기록하며 원문에는 테스트 라벨도 추가하지 않습니다. 같은 글이 이미 양쪽에 검증 전송된 기록이 있으면 다시 보내지 않습니다.

무료 인스턴스는 Oracle 정책상 회수될 수 있으므로 무중단을 보장하지 않습니다. SQLite backup API로 백업하고 SSH로 VM 밖에 보관하세요. 운영 DB를 삭제하면 중복 기록이 사라지므로 초기화하지 마세요.

## Oracle → GitHub Actions 이벤트 연결

`raw_runtime.py`의 `GitHubEventBridge`를 재사용합니다. 별도 Telegram 수집기를
동시에 실행하지 않습니다. `deploy/`와 `raw-deploy/` 모두 원문 런타임
`watcher.py`를 실행해야 합니다. 기존 서버에서 설치 스크립트를 재실행하기
전에 운영 파일을 비교하고, 변경 파일만 백업 후 교체하세요.

`/etc/hotdeal-live.env`에 다음 두 키를 설정합니다. 토큰은 서버에서 숨김
입력으로 등록하고 root:root 0600을 유지하세요. 저장소나 로그에 넣지 않습니다.

```ini
JIRUM_GITHUB_DISPATCH_ENABLED=true
JIRUM_GITHUB_DISPATCH_TOKEN=<server-only token>
```

권장 토큰은 `mw5114-tech/hotdeal-bot`만 선택한 fine-grained PAT이며,
Repository permissions의 Actions를 Read and write로 지정합니다.
GitHub Settings → Developer settings → Personal access tokens → Fine-grained
tokens에서 발급하고 만료 전에 교체합니다. Contents 쓰기나 Workflows 수정
권한은 이벤트 실행 전용 토큰에 필요하지 않습니다.

- 정확한 HTTPS 쿠팡 호스트의 본문 링크·숨김 Telegram 링크만 신호로 사용합니다.
- 최초 신호로부터 20초 동안 묶고, 요청 간격은 최소 75초입니다.
- 신호가 없으면 GitHub API를 호출하지 않습니다. 운영 SQLite에 pending/ack를
  보관하며, 수신 중 새 신호가 생겨도 잃지 않습니다.
- 대상은 `daangn-jirum-manual-fifo.yml`, `ref=main`의 `workflow_dispatch`입니다.
  상품 검증·60분 만료·게시 중복 방지는 기존 GitHub FIFO가 담당합니다.
- 명시적인 4xx 거절(408 제외)은 180초 뒤 재시도합니다. 타임아웃·5xx·전송 중
  종료는 inflight를 유지하고 자동 재전송을 차단합니다. 실제 실행 기록과
  대조하기 전에 inflight를 임의로 지우지 마세요.

연결 검증은 서버 환경으로 기존 `workflow_dispatch(token, smoke_test=True)`를
호출합니다. 이 실행의 `smoke-dispatch-only` 성공 및 `drain-backlog` skipped를
확인하세요. 테스트를 위해 운영 메시지를 다시 수집하거나 `verify`로 보내지 않습니다.
자연 신규 메시지의 `SOURCE_EVENT` → `GITHUB_EVENT_QUEUED` →
`GITHUB_DISPATCH_ACCEPTED`와 실제 Actions 실행까지 대조한 뒤에만 백업 cron을
`17,47 * * * *`로 변경합니다. 그 전에는 기존 5분 cron을 유지합니다.

원문 릴레이·세션·DB 기준선과 카페 설정은 변경하지 않습니다. GitHub 연결만
해제하려면 dispatch flag만 false로 바꾸고 서비스를 재시작합니다.

## 기존 원문 전송 검증 기록

원문·공백·이모지·엔티티 보존, 미디어 제외, 부분 실패, 중복 이벤트, 재시작 후 영수증 유지, catch-up 페이지 처리와 중단 복구 테스트를 Windows와 Ubuntu 서버에서 실행했습니다.
실제 글 `94635`는 PC에서, 새 글 `94636`은 Oracle 서버에서 기존 봇으로 양쪽 전송 성공과 반환 텍스트 일치를 확인했습니다. 테스트는 서로 다른 글이며 과거 글 대량 발송은 하지 않았습니다.
서버 프로세스 강제 종료 후 `NRestarts=1`과 수신 루프 복귀를 확인했습니다. 최종 배포 결과에는 실제 재부팅 후 자동 시작 결과와 운영 전환 상태도 별도 기록합니다.
