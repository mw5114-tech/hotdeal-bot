# Oracle 뽐질 RAW_MODE 감시 서버 → GitHub Actions 이벤트 발행

## 실제 운영 진입점 — 중요

`RAW_MODE.md` 및 `raw-deploy/hotdeal-live.service` 기준 **실제 운영 진입점은 `watcher.py → raw_runtime.py`** 이다.
기존 PR #45의 `jirum_live.py` 연동만 배포하면 **현재 원문 전달형 서버에서는 호출되지 않는다.**
이번 수정은 `raw_runtime.py`에도 연결했으며 기존 텔레그램 개인방·핫딜 MUSE용 그룹 **원문 그대로 재전송** 기능을 유지한다.

- `NewMessage`와 `MessageEdited`, 재연결 후 gap recovery를 모두 처리
- raw text에 보이는 쿠팡 링크와 Telegram `text_link`의 **숨긴 쿠팡 URL** 모두 판별
- 다른 쇼핑몰·일반 공지는 GitHub 요청하지 않음 (텔레그램 알림은 기존처럼 전달)
- 20초 내 몰린 이벤트는 1회 실행으로 묶고 성공 요청 사이 최소 75초 간격
- GitHub가 명시적으로 거절한 HTTP 4xx(408 제외)는 원본 메시지 ID 신호를 SQLite에 보존하고 3분 후 재시도
- 요청 전에 `github_dispatch_inflight_post`를 영구 저장한다. 응답 유실·시간 초과·5xx·전송 중 프로세스 종료는 접수 여부가 불확실하므로 재시작 후에도 자동 재요청하지 않는다. `GITHUB_DISPATCH_UNCERTAIN` 또는 남아 있는 inflight 기록은 GitHub 실행 이력과 FIFO 상태를 대조한 뒤 수동 검토해야 한다. 새 이벤트도 이 검토가 끝날 때까지 보류된다.
- GitHub가 HTTP 204를 반환하면 '요청 접수'로 기록. **카페 발행 성공을 뜻하지는 않는다.**
- 실행된 기존 FIFO workflow가 뽐질 채널을 재검사 → GitHub 저장 상태 기반 순차 발행
- 기존 GitHub 5분 예약은 **서버 실전 작동이 확인될 때까지 유지**
- `JIRUM_GITHUB_DISPATCH_ENABLED` 기본 비활성. GitHub 병합만으로 운영 서버가 자동 업데이트되지는 않음.

## 1. 서버 운영 경로 확인

Oracle 서버에 SSH로 접속한 터미널에서 다음을 실행한다.

```bash
sudo systemctl is-active hotdeal-live
sudo systemctl show -p ExecStart hotdeal-live
sudo journalctl -u hotdeal-live -n 25 --no-pager
```

`ExecStart`가 `/opt/hotdeal-live/watcher.py run`이라면 아래 RAW_MODE 배포 절차가 맞다.
다르면 먼저 실제 실행 파일을 확인해야 하며, 운용 프로세스를 임의로 교체하면 안 된다.

## 2. GitHub Actions API 토큰

GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**에서:
- Resource owner는 자신의 계정, Repository access는 **Only select repositories** → `mw5114-tech/hotdeal-bot` 한 개만
- Repository permissions → **Actions: Read and write**
- 짧은 만료 기간을 정하고 갱신 예정일 기록

토큰은 서버에서만 사용한다. **채팅·로그·GitHub 저장소에 복사하지 않는다.** 기존 봇 토큰을 새로 발급할 필요는 없다.

## 3. 기존 Oracle 서버 코드 배포 (기존 DB/세션 그대로)

서버에서 접근 가능한 해당 저장소 체크아웃 디렉터리에서:

```bash
git pull --ff-only origin main
sudo install -o root -g root -m 0644 raw_runtime.py /opt/hotdeal-live/raw_runtime.py
sudo install -o root -g root -m 0644 jirum_github_dispatch.py /opt/hotdeal-live/jirum_github_dispatch.py
sudo /opt/hotdeal-live/.venv/bin/python -m py_compile /opt/hotdeal-live/raw_runtime.py /opt/hotdeal-live/jirum_github_dispatch.py
sudoedit /etc/hotdeal-live.env
```

기존 `JIRUM_LIVE_ENABLED=true`와 Telegram 설정은 유지하고 두 줄만 추가/변경:

```dotenv
JIRUM_GITHUB_DISPATCH_ENABLED=true
JIRUM_GITHUB_DISPATCH_TOKEN=서버에만_보관하는_fine_grained_PAT
```

```bash
sudo chmod 600 /etc/hotdeal-live.env
sudo systemctl restart hotdeal-live
sudo systemctl is-active hotdeal-live
sudo journalctl -u hotdeal-live --since '10 minutes ago' --no-pager
```

`GITHUB_DISPATCH_ARMED`와 `runtime=raw`를 확인한다.
원문 전달이 정상 동작하는지 확인하고 신규 쿠팡 메시지가 도착하면:
`SOURCE_EVENT` → `GITHUB_EVENT_QUEUED` → `GITHUB_DISPATCH_ACCEPTED`.
실행 이력의 `event=workflow_dispatch`, 게시물 `POST_PUBLISHED_AND_FORMAT_VERIFIED` 및 FIFO 상태를 별도 확인한다.

## 4. 실전 API 연결 테스트 — 글을 쓰지 않는 방법

`.github/workflows/daangn-jirum-manual-fifo.yml`은 `workflow_dispatch` 입력
`smoke_test=true`로 실행하면 **scan/claim/upload/post를 전혀 하지 않는 별도 Job**만 실행한다.

GitHub Actions의 `Jirum | GitHub dispatch smoke (NO CAFE POST)` 워크플로를 한 번 실행하면
GitHub Token으로 **공식 REST API를 실제 호출**하고 HTTP 204 접수 여부를 검사한다.
성공한 뒤 연결된 원본 FIFO 워크플로의 `smoke-dispatch-only` 작업 로그에서
`JIRUM_GITHUB_DISPATCH_SMOKE_OK — NO_SCAN_NO_POST`를 확인한다.

*이 검증은 GitHub 쪽 REST API 수신과 Actions 실행만 증명한다.*
Oracle 서버의 PAT·Telethon 실시간 이벤트는 위 3단계에서 별도 실전 확인해야 한다.

## 5. 전환/장애

서버 이벤트 기반 발행을 실제로 확인한 뒤에만 5분 예약을 끄거나 저빈도 복구용으로 낮춘다.
자동화에 장애가 나면 `/etc/hotdeal-live.env`의
`JIRUM_GITHUB_DISPATCH_ENABLED=false`로 변경 후 서비스를 재시작해
**원본 텔레그램 재전송은 그대로 유지**할 수 있다.
당근카페 게시 결과가 불확실하면 **게시 상태/FIFO 잠금을 먼저 확인**하고 중복 실행하지 않는다.

이 GitHub 연결만으로는 Oracle 서버의 파일 수정, PAT 설정, 프로세스 재시작을 수행할 수 없다.
