# 기존 뽐질 Telethon 감시 서버 → GitHub Actions 이벤트 실행

## 선택한 방식

기존 `jirum_live.py`가 `@jirum`에서 새 메시지를 받을 때, 메시지에 쿠팡 구매 링크가 있으면 GitHub Actions의 **기존 당근카페 FIFO drain**을 공식 REST API(`workflow_dispatch`)로 요청합니다.

- 새 서버, Cloudflare, Telegram 웹훅, 보조 봇 불필요
- 원래 개인 채팅·핫딜 MUSE용 그룹에 텔레그램 봇으로 보내는 기능은 그대로
- 20초 동안 새 상품 이벤트를 묶어 Actions 실행 1회로 처리
- 성공 요청 사이 최소 75초 간격을 두어 실행 남발 방지
- GitHub API 오류 시 대기 신호를 SQLite에 보존하고 180초 뒤 재요청
- GitHub Actions는 기존대로 원본 뽐질 채널을 재검사하고 GitHub state의 `pending`을 오래된 순서로 모두 발행
- 각 상품별 사전 claim, 사진 업로드 완료 확인, 발행 검증 및 불확실 상태 중복 방지 유지
- 텔레그램 감시 서버는 GitHub에 상품 전문이나 인증 정보를 전송하지 않음
- `JIRUM_GITHUB_DISPATCH_ENABLED=false` 기본 비활성. **이 저장소에 PR을 병합하는 것만으로는 실제 서버에 배포되지 않음.**

## 운영 서버에 배포하기 전 조건

현재 운영 중인 서버가 실제로 `jirum_live.py`/systemd `hotdeal-live.service`를 사용 중인지 확인합니다.

서버의 GitHub API 호출에는 비공개 저장소 `mw5114-tech/hotdeal-bot`에 접근 가능한 **fine-grained personal access token**이 필요합니다.

1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Repository access는 `Only select repositories`에서 **`mw5114-tech/hotdeal-bot`만** 선택
3. Repository permissions에서 **Actions: Read and write** 설정
4. 토큰 값은 반드시 실제 서버의 `/etc/hotdeal-live.env`에만 저장하고, GitHub 소스·로그·채팅에 붙여넣지 않습니다.

기존 GitHub Actions 안의 `github.token`은 서버 외부에서 재사용할 수 없습니다.

## 실제 서버 배포 (사용자가 서버 SSH에서 수행)

배포 소스 저장소 디렉터리로 이동하여 최신 main을 반영합니다.

```bash
git pull --ff-only origin main
sudo install -o root -g root -m 0644 jirum_github_dispatch.py /opt/hotdeal-live/jirum_github_dispatch.py
sudo install -o root -g root -m 0644 jirum_live.py /opt/hotdeal-live/jirum_live.py
sudo python3 -m py_compile /opt/hotdeal-live/jirum_github_dispatch.py /opt/hotdeal-live/jirum_live.py
sudoedit /etc/hotdeal-live.env
```

기존 값은 그대로 유지하고 아래 두 값만 설정합니다.

```dotenv
JIRUM_GITHUB_DISPATCH_ENABLED=true
JIRUM_GITHUB_DISPATCH_TOKEN=발급받은_서버전용_토큰
```

설정 파일은 root 전용으로 보관합니다. 기존 `JIRUM_LIVE_ENABLED=true` 및 텔레그램 봇 설정을 변경할 필요는 없습니다.

```bash
sudo chmod 600 /etc/hotdeal-live.env
sudo systemctl restart hotdeal-live
sudo systemctl is-active hotdeal-live
sudo journalctl -u hotdeal-live --since '15 minutes ago' --no-pager
```

로그에서 `GITHUB_DISPATCH_ARMED`를 확인한 뒤, 뽐질 채널에 실제 **신규 쿠팡 메시지**가 도착하면 `GITHUB_EVENT_QUEUED`와 `GITHUB_DISPATCH_ACCEPTED`를 확인합니다. GitHub Actions의 해당 실행 `event=workflow_dispatch`와 당근카페 발행 검증을 별도로 확인합니다. `GITHUB_DISPATCH_ACCEPTED`는 GitHub가 요청을 접수했다는 의미이지 카페 게시 성공을 뜻하지 않습니다.

## 5분 스케줄 전환

**서버 배포·실제 신규 이벤트·GitHub 발행까지 확인하기 전에는 기존 5분 cron을 제거하지 않습니다.**

전환 확인 후에만 `.github/workflows/daangn-jirum-manual-fifo.yml`에서 `schedule`을 제거하거나 백업용 저빈도로 줄입니다. 워크플로의 `workflow_dispatch`, FIFO 처리, concurrency 설정은 유지합니다.

현재 외부 클라우드 서버를 GitHub 연결 도구로 직접 원격 관리할 수는 없기 때문에, GitHub 저장소 코드 수정과 운영 서버 배포는 별도의 작업입니다.
