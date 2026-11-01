# 핫딜 MUSE용 그룹 → GitHub Actions (오라클 수정 없음)

## 목적

기존 Oracle 서버·텔레그램 원문 재전송 봇은 **일절 수정하지 않음**.
Oracle이 그룹에 이미 전달하고 있는 메시지를 **별도 Telegram 리스너 봇**이 받아서
무료 Cloudflare Worker 웹훅에 전송한다. Worker는 Cloudflare Queue에서
**최대 20초 동안 새 글을 묶어 GitHub Actions를 한 번만 실행**한다.

```text
기존 Oracle 봇 → 기존 핫딜 MUSE용 텔레그램 그룹
                                 ↓
                Bot-to-Bot 수신 허용된 리스너 봇
                                 ↓ Telegram webhook
                  Cloudflare Worker (무료)
                                 ↓ Queue (최대 20초 묶음)
        GitHub workflow_dispatch (기존 FIFO drain)
                                 ↓
                    당근카페 기존 자동 발행
```

**현재 GitHub에서 테스트 코드가 통과했다고 Telegram 리스너의 실제 webhook이
연결된 것은 아니다.** Cloudflare 계정과 리스너 봇 토큰이 필요하며,
새 연결을 실제로 켜기 전에는 기존 GitHub 5분 schedule을 유지한다.

## 텔레그램 최신 기능

공식 문서: https://core.telegram.org/bots/features#bot-to-bot-communication

- 기존 그룹에서 **받는 쪽 리스너 봇**을 @BotFather의 My Bots → Bot Settings에서
  *Bot-to-Bot Communication Mode* ON으로 설정한다.
- 그 리스너가 **그룹 관리자이거나 그룹 개인정보 보호 모드가 OFF**라면
  다른 봇이 보낸 일반 그룹 메시지까지 받을 수 있다.
- 보내는 봇은 변경하지 않아도 된다.
- 사진 속 `핫딜 리스너`는 관리자 표시가 있으므로 후보지만,
  **이미 다른 프로그램이 사용 중이라면 해당 봇의 webhook을 뺏지 않는다.**
  기존에 사용 중이면 새 수신 전용 봇을 하나 만들고 그룹 관리자로 추가하면 된다.

## 기존 봇 확인: 충돌 방지 필수

받는 리스너 봇의 토큰을 가진 사람만 연결할 수 있다. 먼저 다음 Bot API를 호출해
`getWebhookInfo.result.url`을 확인한다.

`https://api.telegram.org/bot<리스너_봇_토큰>/getWebhookInfo`

- url이 이미 설정돼 있으면 **절대 무작정 setWebhook 하지 말 것**.
- url이 비어 있어도 기존 장치가 `getUpdates` 롱폴링을 하고 있을 수 있으므로
  기존 리스너의 사용 여부를 확인한다.
- **서로 다른 프로세스가 같은 봇 토큰으로 getUpdates/webhook 수신을 경쟁하지 않게 한다.**
- 새로 만들거나 현재 수신 서비스가 없는 전용 리스너 봇을 사용하는 게 가장 간단하다.
- 기존 `이 가격 못 참지 BOT`은 메시지를 전송하는 역할 그대로 둔다.

## 1. Cloudflare 무료 Worker 및 Queue 배포

Node.js와 Cloudflare 계정이 필요하다.

```bash
cd group-trigger-worker
npx wrangler login
npx wrangler queues create muse-telegram-github-dispatch
```

`wrangler.toml`의 `SOURCE_SENDER_BOT_ID = "0"`을
**기존 메시지를 보내는 봇**의 실제 숫자 ID로 변경한다.
ID가 0이면 워커가 의도적으로 메시지를 거절하므로 실수로 실행되지 않는다.
그룹 ID는 기존 저장소 `state/telegram_group_chat.json`의 실제 값을 넣어둠.

송신 봇 ID 확인은 GitHub Actions → **Inspect MUSE sender bot ID (read-only)**
수동 실행으로 확인할 수 있다. 이 작업은 기존 GitHub Secret
`TELEGRAM_BOT_TOKEN`을 통해 Bot API `getMe`만 조회하고 ID만 출력한다.
보낸 봇이 사진 속 `이 가격 못 참지 BOT`과 같은지 username을 대조한다.

보호할 비밀정보는 Cloudflare Worker에만 등록한다.

```bash
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
npx wrangler secret put GITHUB_ACTIONS_TOKEN
npx wrangler deploy
```

- `TELEGRAM_WEBHOOK_SECRET`: 영문·숫자·`_`·`-`만 포함하는
  임의의 길고 고유한 값 (Telegram `setWebhook.secret_token`에 같은 값 사용)
- `GITHUB_ACTIONS_TOKEN`: GitHub fine-grained PAT.
  Repository `mw5114-tech/hotdeal-bot` 한 개만 선택,
  **Actions: Read and write** 권한만 부여
- Cloudflare에 **텔레그램 리스너 봇 토큰은 저장하지 않음**;
  최초 webhook 설정할 때만 사용

Worker가 표시하는 `https://...workers.dev` URL을 복사한다.

## 2. Telegram 리스너의 webhook 연결

Windows PowerShell 예시. **다른 서비스에서 사용하지 않는 리스너 봇**에서만 실행.

```powershell
$token = Read-Host "리스너 봇 토큰"
$secret = Read-Host "Worker에 등록한 TELEGRAM_WEBHOOK_SECRET"
$workerUrl = Read-Host "https://....workers.dev (마지막 / 없이)"

# 기존 webhook URL부터 꼭 확인
$info = Invoke-RestMethod "https://api.telegram.org/bot$token/getWebhookInfo"
$info.result.url
# 빈 문자열이 아니거나 기존 폴링 서비스가 있다면 연결하지 말 것.

# 앞의 기존 webhook/폴링 서비스 미사용 확인 후에만 실행
Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/setWebhook" -Body @{
  url = "$workerUrl/telegram-webhook"
  secret_token = $secret
  allowed_updates = '["message","edited_message"]'
  drop_pending_updates = 'false'
}

Remove-Variable token,secret
```

**토큰 및 GitHub PAT를 채팅, 레포지토리 파일 또는 출력 스크린샷에 올리지 말 것.**
Telegram의 웹훅은 새 메시지를 받는 즉시 Cloudflare HTTPS endpoint로 POST한다.

## 3. 실전 검증

1. GitHub Actions에서 이 레포의 `Tests | MUSE group listener to GitHub (offline)`
   성공을 확인한다. 해당 테스트는 절대 Telegram이나 카페에 실전 요청하지 않는다.
2. 리스너 봇의 Bot-to-Bot Communication ON, 관리자/Privacy OFF,
   정확한 기존 송신 봇 ID와 그룹 ID가 입력됐는지 확인한다.
3. 실제 `이 가격 못 참지 BOT`이 그룹에 **새 쿠팡 링크 메시지**를 전송하면
   Cloudflare Worker → Queue에 메시지가 하나 쌓여야 한다.
4. 대략 20초 묶음 후 Cloudflare Worker 로그에 `DISPATCH_ACCEPTED`가 나오는지
   확인한다. 이것은 GitHub 204 *요청 접수*이며 카페 게시 성공 확정은 아니다.
5. GitHub Actions에서 `Jirum Coupang | Daangn FIFO drain` 실행의
   **event가 workflow_dispatch**인지 확인하고,
   상태파일 `pending` 및 게시 결과를 검사한다.
6. 오라클 서버의 텔레그램 원문 전달과 기존 GitHub FIFO 중복 방지가
   그대로 작동하는지 확인한다.

**GitHub가 막힌 경우 Cloudflare Queue는 최대 20회 재시도**하며
무료 플랜에서는 메시지 최대 24시간 보관.
GitHub가 요청을 수락했지만 응답이 유실되면 GitHub 실행 요청 자체는
중복될 수 있지만, 기존 FIFO 게시물 상태와 claim은 중복 게시를 방지하도록 설계됨.

실전 성공 확인 전까지 기존 5분 GitHub schedule **유지**.
실전 성공 뒤에는 사용량 절감을 위해 5분 schedule을 제거하고
30분~1시간 간격의 복구용 확인만 남기는 것을 권장.

## 비용

Cloudflare Workers Free: 하루 100,000 요청(계정 전체 기준)
Cloudflare Queues Free: 하루 10,000 operations
(메시지 하나당 일반적으로 write/read/delete 3 operations).
일반 핫딜 채널 규모에서 무료 한도 안에 들어올 가능성이 높다.
**GitHub의 비공개 저장소 Actions 런타임 사용량은 별도**이므로
이벤트 발생 빈도가 높다면 Github 사용량을 계속 확인한다.

공식 문서:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/queues/platform/limits/
- https://developers.cloudflare.com/queues/configuration/batching-retries/
- https://core.telegram.org/bots/api#setwebhook
