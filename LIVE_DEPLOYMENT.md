# 뽐질 상시 감시 — 배포와 전환

## 현재 상태

새 프로그램 `jirum_live.py`는 Telethon 1.45.0 `NewMessage` / `MessageEdited`를 사용합니다.
기존 딜스팟, 네이버, 뽐질 Actions와 기존 전송 모듈은 그대로 유지합니다.
기본값은 `JIRUM_LIVE_ENABLED=false`입니다. 설치 스크립트는 서비스를 시작하지 않습니다.
클라우드 계정, 서버, 실제 인증값이 준비되기 전에는 24시간 운영이 완료된 것이 아닙니다.

## 처리 방식

- 소스는 `@jirum` 한 채널로 고정합니다. 다른 대화 목록이나 개인 메시지 기록을 조회하지 않습니다.
- `telegram_delivery.target_chats()`의 기존 개인방·그룹 설정을 재사용합니다. 두 대상이 모두 있어야 합니다. 실제 전송 전 Bot API로 그룹 제목 `핫딜 MUSE용`과 개인 채팅 종류를 확인합니다.
- 원문/캡션의 줄바꿈, 가격, 할인, 외부 URL을 그대로 보존합니다. 숨겨진 링크는 Telethon의 UTF-16 엔티티 처리를 사용합니다. 외부 구매 URL을 접속하거나 단축 해제하거나 제휴 URL로 바꾸지 않습니다.
- 상품명은 기존 추출기를 재사용한 휴리스틱입니다. 구매 링크 후보가 여러 개면 구매·특가·바로가기 라벨의 링크를 우선합니다. 그 외에는 첫 외부 링크를 고릅니다. 상품 링크를 찾지 못한 글은 전송하지 않고 수정 이벤트를 기다립니다. 미디어 파일 자체는 재업로드하지 않습니다.
- 형식은 `🆕 [HOTDEAL]`, `상품명:`, `상품링크:`, `원문:`입니다. 추가 숨겨진 URL은 원문 뒤에 표시합니다. 긴 메시지는 UTF-16 3,500 단위 이하로 나눠 원문을 자르지 않습니다. 각 조각은 양쪽에 동일하게 전송합니다.
- 최초 운영 실행은 현재 최신 글 ID를 기준선으로만 기록합니다. 명시적인 전환 체크포인트가 있으면 `arm` 명령으로 그 이후부터 이어받습니다.
- SQLite WAL + synchronous FULL로 게시글 ID와 대상/조각별 전송 결과를 기록합니다. 로컬 프로세스 잠금으로 같은 데이터 디렉터리의 중복 실행을 막습니다. 디스크나 DB를 지우면 기록도 없어지므로 영구 디스크와 백업이 필요합니다.
- 수신 이벤트와 재연결 시 `catch_up`을 사용하고, 60초 간격으로 해당 채널의 누락 기록만 MTProto로 보완합니다. 웹페이지 5분 폴링이 기본 감지 수단인 구조가 아닙니다. 원문이 서버에서 삭제된 글은 복원하지 못합니다.
- 수정 글이 아직 전혀 전송되지 않았다면 최신 내용으로 교체합니다. 어느 대상에든 한 번 전송을 시도한 이후에는 양쪽 내용 일치를 위해 기존 페이로드를 고정합니다. 이미 보낸 글의 수정은 재발송하지 않습니다.
- 명시적 429와 연결 전 DNS/접속 거부는 재시도합니다. 명시적 API 거절은 `blocked`, 타임아웃/응답 유실/전송 중 프로세스 종료는 `uncertain`입니다. 성공한 대상은 반복하지 않습니다. Bot API에는 이 전송의 멱등 키가 없으므로 장애 시 절대적인 exactly-once 보장은 불가능합니다. `uncertain`은 자동 재시도하지 않고 운영자가 채팅에서 확인합니다.
- 로그에는 게시글 ID/상태/성공 응답 ID만 남깁니다. Bot Token, API Hash, 원문, 응답 본문, 전화번호는 출력하지 않습니다. 애플리케이션 DB에는 `@jirum` 원문과 전송 내용이 저장되므로 비공개로 관리합니다.

## Oracle 가입 및 무료 자원

[무료 계정 가입](https://signup.cloud.oracle.com/)은 사용자가 이메일 확인, 비밀번호 설정, 카드/본인 인증, OTP 및 약관 동의를 직접 완료합니다. 이 정보를 AI 채팅에 보내지 마세요.

2026-10-31 확인한 [Oracle 공식 Always Free 문서](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)는 홈 리전의 A1 무료 범위를 월 1,500 OCPU 시간/9,000 GB 시간(상시 2 OCPU/12 GB 상당), 볼륨 합계 200 GB로 설명합니다. 이전의 4 OCPU/24 GB 안내를 그대로 적용하지 마세요. 현재 계정의 콘솔 표시와 이미 사용 중인 자원을 반드시 확인합니다.

이 봇은 Ubuntu의 A1 1 OCPU / 메모리 1–2 GB 또는 무료 E2.1.Micro 한 대면 충분한 규모입니다. 홈 리전에서 **Always Free Eligible**인 Ubuntu 이미지·인스턴스·부트 볼륨 조합만 선택합니다. 50 GB 부트 볼륨 하나면 충분합니다. 유료 자원, Marketplace 이미지, 유료 업그레이드는 만들지 않습니다. 콘솔이 Trial Credit 비용만 표시하거나 무료 여부가 불분명하면 생성하지 말고 확인합니다.

`Out of host capacity`는 무료 자원 가용량 부족입니다. 무료 범위의 다른 가용 도메인 또는 나중 재시도가 대안이며, 유료 전환은 별도 허락이 필요합니다. 대안은 이미 보유한 상시 실행 서버나 다른 제공자의 현재 무료 VM이지만 해당 무료 조건부터 확인해야 합니다. GitHub Actions 장기 실행은 상시 서버 대체로 사용하지 않습니다.

Oracle은 저사용량 무료 인스턴스를 회수할 수 있습니다. 따라서 무료 VM의 무중단 24/7을 보장할 수는 없습니다. 불필요한 부하로 회수를 회피하지 말고 일관된 DB 백업과 상태 점검을 준비합니다.

## 네트워크와 SSH

- 웹서버/공개 HTTP 포트/로드밸런서는 필요 없습니다. Telegram 아웃바운드 연결을 사용합니다.
- 초기 관리용 SSH 22번은 사용자의 공인 IP `/32`로 제한합니다. 80/443 인바운드는 열지 않습니다.
- 서버 SSH 호스트키 지문을 콘솔 등 독립 경로로 확인하고 접속합니다. `StrictHostKeyChecking=no`를 사용하지 않습니다.
- SSH 개인키는 PC에만 보관합니다. 채팅에는 서버 IP, SSH 사용자명, 키 파일 **경로**만 전달합니다.

## 설치

서버가 준비되면 개발 담당자가 다음 작업을 수행할 수 있습니다. 코드 전송은 비밀정보가 없는 배포 압축 파일을 SCP로 옮기거나 비공개 저장소를 안전하게 체크아웃합니다. GitHub 토큰을 URL/셸 기록에 넣지 않습니다.

```bash
tar -xzf hotdeal-live-deploy.tar.gz
cd hotdeal-live
sudo bash deploy/install.sh
sudo python3 deploy/configure_live.py --output /etc/hotdeal-live.env
```

`configure_live.py`는 사용자 터미널에서 API ID, 숨김 입력 API Hash/Bot Token/개인 Chat ID를 받습니다. GitHub Secrets의 값은 읽어서 복구할 수 없으므로 기존 값을 사용자가 직접 입력해야 합니다. 새 토큰을 발급받아 기존 봇을 바꿀 필요는 없습니다. 설정은 root 전용 0600이고 자동 전송은 false로 고정됩니다. 기존 파일은 덮어쓰지 않습니다.

Windows에서 로컬 API ID/Hash만 재사용하려면 다음 도구를 **사용자가 직접** 실행할 수도 있습니다. 코드를 import/실행하지 않고 두 리터럴 할당만 읽습니다. 출력 파일과 해당 폴더의 Windows ACL을 사용자 전용으로 제한하고, SCP 후 임시 설정 파일을 정리하세요.

```powershell
python deploy/configure_live.py --output .env --local-watcher C:\Users\User\hotdeal-live\watcher.py
```

## Telethon 인증 세션

로컬 `C:\Users\User\hotdeal-live\hotdeal_session.session` 파일이 존재함은 확인했습니다. 내용이나 다른 대화는 읽지 않았습니다.

1. 로컬 기존 watcher를 정상 종료합니다. 세션을 PC와 서버에서 동시에 쓰지 않습니다. 서버 전환 후에는 PC watcher를 다시 실행하지 않습니다.
2. 종료 후 `.session-journal`, `.session-wal` 등이 남으면 세션 DB를 임의로 복사하지 말고 정상 종료/체크포인트를 확인합니다. 파일이 계속 사용 중이면 서버에서 별도 로그인하는 편이 안전합니다.
3. SSH 지문을 확인한 뒤 SCP로 세션을 서버 사용자의 비공개 임시 경로에 전송합니다. 예시의 IP/키 경로는 실제 값으로 바꿉니다.

```powershell
scp -i "C:\path\server-key" "C:\Users\User\hotdeal-live\hotdeal_session.session" ubuntu@SERVER_IP:~/hotdeal_session.session
```

```bash
sudo install -o hotdeal-live -g hotdeal-live -m 0600 ~/hotdeal_session.session /var/lib/hotdeal-live/hotdeal_session.session
# 서비스가 사용할 세션이 정상인지 확인한 뒤 임시 복사본을 지웁니다.
```

세션은 계정 로그인 권한입니다. GitHub/Actions artifacts/일반 로그/채팅에 올리지 않습니다. 계정의 다른 대화까지 담지 않도록 서버의 새 인증 세션을 사용자가 직접 생성하는 대안도 있습니다.

```bash
sudo systemd-run --pty --wait --collect \
  --property=User=hotdeal-live --property=Group=hotdeal-live \
  --property=EnvironmentFile=/etc/hotdeal-live.env \
  --property=WorkingDirectory=/opt/hotdeal-live \
  /opt/hotdeal-live/.venv/bin/python /opt/hotdeal-live/deploy/login_session.py
```

이 대안에서는 사용자가 SSH 터미널에서 전화번호/로그인 코드/2FA를 직접 입력합니다. AI가 인증 프롬프트를 대신 조작하지 않습니다.

## 사전 검증 → 관찰 모드 → 실전 전송 테스트

```bash
cd /opt/hotdeal-live
sudo bash deploy/run.sh check
sudo systemctl enable --now hotdeal-live
sudo systemctl status hotdeal-live --no-pager
sudo journalctl -u hotdeal-live -n 30 --no-pager
```

설정은 아직 false이므로 별도 `jirum-shadow.sqlite3`에 관찰 기록만 남습니다. `BASELINE_ONLY`와 `WATCHING enabled=false`를 확인하고, 신규 실제 채널 이벤트 `SOURCE_EVENT`가 수신되는지 봅니다. 이때 기존 Actions는 계속 동작합니다.

실전 메시지 검증은 서비스와 세션을 동시에 열지 않도록 서비스를 잠시 중지한 뒤 수행합니다.

```bash
sudo systemctl stop hotdeal-live
sudo bash deploy/run.sh verify
sudo bash deploy/run.sh status --database test
sudo systemctl start hotdeal-live
```

`verify`는 최근 글 한 건을 기존 봇으로 양쪽에 보냅니다. 수동 테스트 표기가 붙고 `jirum-test.sqlite3`의 고유 테스트 scope로 기록합니다. 운영 기준선에는 영향이 없습니다. 두 대상의 모든 조각이 성공해야 `TEST_DELIVERY_OK`입니다. `verify`를 다시 실행하면 의도적인 새 수동 테스트 전송이므로 이전 테스트가 반복될 수 있습니다. 테스트 실패 후 무작정 반복하지 말고 상태를 확인합니다.

그 뒤 서비스 재시작, 재부팅, 재연결 및 이벤트 수신을 검증합니다.

```bash
sudo systemctl restart hotdeal-live
sudo systemctl is-enabled hotdeal-live
sudo systemctl is-active hotdeal-live
# 허가된 점검 시간에 재부팅 후 위 상태와 journalctl을 다시 확인합니다.
```

관찰 모드가 정상이라는 것만으로 운영 전환하지 않습니다. 실제 서버에서 두 대상 전송 영수증·재시작·신규 수신을 모두 확인해야 합니다.

## 운영 전환 — 아직 실행하지 않은 절차

1. 개인/그룹 실제 전송 성공과 서버 재시작 검증을 완료합니다. 이때까지 기존 `.github/workflows/jirum-forward.yml` 예약 실행을 유지합니다.
2. 서버 관찰 서비스를 중지합니다. **뽐질 워크플로의 schedule 항목만** 제거해 새 예약 실행을 막고, 기존 실행 중인 뽐질 잡이 끝날 때까지 기다립니다. `BOT_ENABLED`는 딜스팟/네이버와 공유하므로 변경하지 않습니다.
3. 남겨 둔 `workflow_dispatch`로 기존 뽐질 `mode=run`을 한 번 실행해 남은 글을 처리합니다. 전체 성공과 상태 커밋을 확인합니다. 실패/부분 성공이 있으면 전환하지 않습니다.
4. 최신 main의 `state/jirum_last_forwarded.json`에서 성공한 `last_seen_id`를 읽습니다. 문서 작성 당시 값을 복사해 쓰지 말고 방금 실행 후의 값을 사용합니다.
5. 새 운영 DB가 아직 없을 때 아래 `arm`으로 그 ID를 기준선/커서에 넣습니다. 24시간 내 같은 두 대상의 수동 테스트 성공 기록이 있어야 합니다.

```bash
sudo bash deploy/run.sh arm --cutover-id ACTUAL_FINAL_ACTIONS_CHECKPOINT
```

6. `sudoedit /etc/hotdeal-live.env`에서 **JIRUM_LIVE_ENABLED만 true**로 바꾸고 서비스를 시작합니다. 운영 DB는 shadow와 별도이므로 관찰 중 쌓인 기록을 대량 재발송하지 않습니다. `arm` 이후 글은 순서대로 복구합니다.
7. 신규 실제 글 한 건이 개인방/그룹 양쪽에 도착했는지 확인합니다. 운영 서비스 재시작 후 동일 글이 다시 오지 않는지도 확인합니다. 그 뒤 PC를 꺼도 서버가 계속 수신하는지 확인합니다.

운영 DB를 이미 만들었다면 삭제/초기화해서 `arm`을 억지로 통과시키지 마세요. 전환 중 두 시스템을 동시에 운영 전송 모드로 켜지 않습니다. 롤백 시에도 서버 전송을 먼저 멈추고 양쪽의 마지막 성공 기록을 비교한 뒤 Actions 기준선을 조정해야 합니다. 불확실한 전송을 건너뛰거나 다시 보내는 결정을 자동으로 하지 않습니다.

## 운영·장애 처리

```bash
sudo journalctl -u hotdeal-live --since '1 hour ago' --no-pager
sudo bash /opt/hotdeal-live/deploy/run.sh status --database live
```

`uncertain`은 해당 채팅에서 실제 메시지가 있는지 확인합니다. 서비스를 중지한 뒤 대상·게시글·조각별로 처리합니다. `sent`는 이미 도착함을 확인한 경우, `retry`는 미도착을 확인하고 중복 가능성을 이해한 경우에만 사용합니다.

```bash
sudo systemctl stop hotdeal-live
sudo bash /opt/hotdeal-live/deploy/run.sh resolve --database live --post POST_ID --destination personal --part 0 --outcome sent
sudo systemctl start hotdeal-live
```

`blocked`는 봇의 그룹 권한/토큰/채팅 설정을 먼저 정상화한 뒤 `--outcome retry`로 재시도합니다. 이미 보낸 다른 대상은 반복하지 않습니다. 대상 ID가 바뀌면 기록을 자동 재해석하지 않고 `DESTINATIONS_CHANGED_REVIEW_REQUIRED`로 멈춥니다.

DB는 SQLite backup API로 일관된 복사본을 만들고 SSH로 VM 밖에 보관합니다. 실행 중인 WAL DB 파일만 단순 복사하지 않습니다. `.session`은 별도의 민감한 인증 파일이므로 DB 백업 도구에 포함되지 않습니다.

```bash
sudo -u hotdeal-live /opt/hotdeal-live/.venv/bin/python /opt/hotdeal-live/deploy/backup_state.py \
  /var/lib/hotdeal-live/jirum-live.sqlite3 /var/lib/hotdeal-live/backup-UNIQUE.sqlite3
```

## 검증 구분

로컬 테스트는 실제 Telethon 엔티티와 가짜 Bot/채널로 실행하며, 실전 인증값을 사용하지 않습니다. CI는 Ubuntu에서 전체 회귀 테스트와 셸 문법을 검사합니다. **CI 통과는 Oracle 서버 배포, systemd 실제 재부팅, Telethon 실제 재연결, 개인/그룹 실전 전송 성공을 의미하지 않습니다.** 이 항목들은 계정과 서버 접근이 준비된 뒤 따로 기록해야 합니다.

참고: [Telethon 이벤트](https://docs.telethon.dev/en/stable/quick-references/events-reference.html), [재연결·catch_up](https://docs.telethon.dev/en/stable/modules/client.html), [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage).
