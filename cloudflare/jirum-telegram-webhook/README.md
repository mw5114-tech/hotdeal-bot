# Telegram 그룹 이벤트 → GitHub Actions → 당근카페 FIFO

기존 Oracle 수신기를 재사용하는 경로는 [Oracle 배포 안내](../../docs/ORACLE_GITHUB_DISPATCH.md)를 참고하세요. 두 실행기를 동시에 활성화하지 않습니다.

## 원리
- 새로 들어온 **텔레그램 그룹 메시지 이벤트**를 Cloudflare Workers 웹훅이 수신합니다.
- 지정한 그룹 채팅 ID + 지정한 릴레이 봇 ID를 모두 통과하고, 본문 또는 링크 엔티티에 **쿠팡 상품 URL**이 있는 경우에만 처리합니다.
- BotFather에서 **Bot-to-Bot Communication Mode**를 활성화한 수신용 봇은 같은 그룹의 다른 봇 메시지를 받을 수 있습니다. 그룹 관리자 권한이 있거나 그룹 Privacy Mode를 끈 상태여야 일반 봇 메시지를 모두 수신합니다.
- D1 SQLite 데이터베이스에서 (chat_id, message_id)를 유일 키로 저장해 텔레그램 재전송/중복 이벤트를 제거합니다.
- GitHub Actions 워크플로는 **daangn-jirum-manual-fifo.yml** 기존 것을 그대로 사용합니다. 이 워크플로는 GitHub 저장소의 FIFO 상태에서 가장 오래된 상품 1개만 발행하고, 실패·불확실한 발행은 차단합니다.
- 매분 실행되는 Cloudflare Cron은 D1 대기 이벤트나 GitHub FIFO backlog만 확인합니다. **새 쿠팡 이벤트와 FIFO backlog가 모두 없으면 GitHub Actions를 실행하지 않습니다.** GitHub Action 과금 시간을 보존합니다.
- 운영 상한은 예시로 **최근 24시간 12회 dispatch**로 설정했습니다. 실제 게시글이 많으면 미처리 상품이 대기할 수 있습니다.
- **초기 ENABLE_DISPATCH=false**이므로 소스 코드 배포나 병합만으로 GitHub Actions가 실행되지 않습니다.
- *이 Worker 자체는 ChatGPT 모델을 항상 깨워 두는 기능이 아닙니다.* 봇 API 업데이트와 GitHub API를 연결하는 경량 코드입니다.

## 설치 준비
1. Cloudflare 무료 계정, Worker, D1 데이터베이스 필요.
2. Telegram 봇 토큰 필요. **기존 봇에서 getUpdates polling이나 웹훅을 이미 사용하고 있다면 이 새 웹훅으로 교체하면 기존 봇의 수신이 끊길 수 있습니다.** 전용 리스너 봇을 쓰거나 기존 봇 프로세스 안에 이 연동을 통합하세요.
3. 그룹에 봇을 추가하고 필요하면 관리자 권한 부여. @BotFather 설정에서 Bot-to-Bot Communication Mode 켜기.
4. 실제 메시지를 보내는 원본 릴레이 봇의 숫자 ID, 그룹의 숫자 채팅 ID 필요. 봇 사용자명만으로 검증하지 않습니다.
5. GitHub fine-grained Personal Access Token: **mw5114-tech/hotdeal-bot 저장소 한정**, Actions: write, Contents: read. Cloudflare Secret으로 설정. 공개 채팅이나 저장소에 토큰을 기록하면 안 됩니다.

## 배포
Cloudflare 무료 계정에서:

    cd cloudflare/jirum-telegram-webhook
    npx wrangler login
    npx wrangler d1 create jirum-telegram-trigger

명령 결과의 D1 database_id를 wrangler.toml의 REPLACE_WITH_D1_DATABASE_ID 자리에 넣습니다.

    npx wrangler d1 migrations apply jirum-telegram-trigger --remote
    npx wrangler secret put TELEGRAM_GROUP_CHAT_ID
    npx wrangler secret put TRUSTED_RELAY_BOT_ID
    npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
    npx wrangler secret put GITHUB_TOKEN
    npx wrangler deploy

- secret 입력값은 Cloudflare에만 저장합니다. 예제 저장소에는 토큰/그룹 ID가 없습니다.
- 배포 시 Workers 도메인 URL을 확인합니다.
- Telegram Bot API setWebhook에서 https://<worker-host>/telegram/webhook 주소를 등록하고, allowed_updates를 ["message"]로 선택합니다. secret_token은 Cloudflare TELEGRAM_WEBHOOK_SECRET과 똑같아야 합니다.
- URL에 사용되는 **봇 토큰은 @BotFather에서 받은 토큰**입니다. 환경변수로 다루고 저장소에 넣지 마세요.
- ENABLE_DISPATCH=false에서는 인증된 메시지를 D1에 저장하고 200을 반환하지만 GitHub를 호출하지 않습니다. 먼저 수신 전용 상태에서 검증하세요. 테스트 이벤트는 활성화 전에 검토해야 합니다.
- 그룹에서 실제 봇 메시지가 보이는지, @BotFather의 Bot-to-Bot Communication Mode가 켜졌는지 먼저 확인하세요. 그 다음 ENABLE_DISPATCH="true"로 변경·배포하고 Telegram setWebhook으로 연결하세요. 활성화 이후 테스트 메시지도 조건에 맞으면 실제 GitHub Actions를 부를 수 있습니다.
- 리스너 봇이 이미 다른 웹훅 또는 getUpdates 사용 중이라면 충돌하지 않도록 기존 프로세스에 코드를 통합하는 방법을 선택해야 합니다.

## 주의
- **원본 @jirum 공개 채널에 수신 봇이 소속돼 있지 않으면 채널 새 글을 직접 받지 못합니다.** 원본을 그룹에 중계하는 릴레이 봇이 필요합니다. 지금 사진 속 '이 가격 못참지 BOT' 또는 다른 릴레이가 실제로 원본 메시지를 그룹에 보내는지 먼저 확인해야 합니다.
- 메시지를 보낸 봇 숫자 ID와 그룹 채팅 ID를 지정하므로, 다른 봇/그룹의 동일 쿠팡 링크는 트리거하지 않습니다.
- Telegram 메시지의 forward_origin.channel 메시지 ID 또는 본문의 t.me/jirum/<id>를 확인할 수 있으면 **94743 이하 기존 게시 건**은 무시합니다.
- GitHub 워크플로가 실제로 상품을 게시하면 영구 FIFO 상태(state/daangn_jirum_manual_fifo.json)가 업데이트됩니다.
- 텔레그램 메시지 중복, 네트워크 타임아웃, GitHub 응답 불명확 등의 경우 동일 요청을 무조건 재시도하지 않습니다. `dispatch_uncertain` 상태는 모든 자동 재호출을 차단하므로 GitHub runs와 FIFO 상태를 직접 확인 후 수동으로 처리해야 합니다.
- 원래 쿠팡 파트너스 링크는 공식 발급/수익 귀속이 검증되지 않은 AFFSDP 후보입니다. 자동 수익 보장을 의미하지 않습니다.
- 1분마다 돌아가는 것은 **Cloudflare 측 복구용 확인만**이고, GitHub Actions가 매분 실행되지는 않습니다.
- Cloudflare Worker, D1 무료 사용량과 GitHub Actions 분량은 별도 제한입니다. 실제 계정의 요금제 사용량을 확인하세요.

## Durable dispatch / direct group payload update

Apply migration `0002_dispatch_attempts.sql` before deploying the updated Worker.
Every POST, including backlog-only dispatches, first creates a unique active
`dispatch_attempts` row. A lost response stays uncertain indefinitely. Accepted
requests must match exactly one `telegram-<request UUID>` workflow run and complete
successfully before further dispatches. Failed runs no longer auto-retry after a
30-minute cooldown. Legacy events without captured payload require manual review.

The webhook now stores exact text, purchase URL and group/message/source IDs in
`payload_json`. Actions receives this as a JSON input (environment variable, never
shell interpolation), saves it to the existing FIFO, and consumes the UUID before
publishing. Group-only products no longer require a matching public-channel post.
The existing manual public scan and cafe publisher remain available.
