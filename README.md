# 현재 핫딜 발행 자동화 (2026-11-10)

- **메인 / 활성**: [NAVER BLOG | MAIN - Telegram to Blog](.github/workflows/naver-blog-auto-main.yml) — 오라클 이벤트 트리거와 30분 백업 예약으로 네이버 블로그 자동 발행.
- **당근 / 보관**: [archive/daangn/README.md](archive/daangn/README.md) — 자동 실행 경로에서 완전 분리, 나중에 복원 가능하도록 코드/상태는 유지.
- **구버전 오라클 연결용**: `.github/workflows/daangn-jirum-manual-fifo.yml` (파일명만 옛 이름인 **네이버 전용 전달기**, 당근 기능 없음).
- **저장 상태**: `state/naver_blog_jirum_fifo.json`과 `state/daangn_jirum_manual_fifo.json` 모두 기존 기록 보존.

---

# 딜스팟 + 네이버 카페 신규 등록 텔레그램 알림 봇

한 번의 GitHub Actions 예약 실행으로 **딜스팟과 네이버 카페를 각각 감시**합니다. PC가 꺼져 있어도 동작합니다.

## 감시 대상 및 알림 방식

| 사이트 | 감시 페이지 | 기준 상태 파일 |
| --- | --- | --- |
| 딜스팟 | https://deal.korea-inc.com/deals/ | `state/seen_products.json` |
| 네이버 카페 | https://cafe.naver.com/f-e/cafes/24651941/menus/0?viewType=L | `state/naver_seen_articles.json` |

- 각 사이트 **첫 페이지**만 조회합니다. 딜스팟은 상품 URL, 네이버 카페는 게시글 번호로 새로운 등록을 구분합니다.
- 딜스팟은 가격·할인율·카테고리 필터 없이 전체 신규 상품을 알립니다. 네이버 카페는 공개된 글 목록(제목/번호)을 로그인 **없이** 읽으며, 공지글을 제외하고 **제목 맨 앞에 정확히 `[ㅋㅍ]` 또는 `[ㅋㅍ프레시]`가 붙은 새 글만** 알립니다. `[알리]`, `[네이버]`, `[11번가]`, `[ㅋㅍ일부회원]` 등은 제외합니다.
- 기준 목록에 없는 항목만 텔레그램 봇으로 제목과 링크를 전송합니다. 전송에 성공한 항목은 저장하며, 한 사이트당 한 번에 최대 10건 전송합니다.
- 네이버 카페의 **신규 비쿠팡 글은 알림 없이 확인 기록에만 추가**하여 재검사 시 중복 처리되지 않게 합니다. 쿠팡 글은 텔레그램 전송에 성공해야만 전송 완료로 기록합니다.
- 상태 파일이 없을 때는 현재 보이는 항목을 **기존 항목으로 저장만 하고 알림을 보내지 않습니다**.
- 사이트별 상태 파일은 **독립적**입니다. 삭제·초기화하지 마세요.
- 현재 코드는 두 사이트 모두 최신 등록이 첫 페이지에 먼저 표시된다고 가정합니다. 두 검사 사이에 한 페이지 이상 밀린 글/상품은 놓칠 수 있습니다.
- 실제 신규 등록의 텔레그램 전송은 해당 사이트에 새 항목이 나타나야 현장 검증이 가능합니다.

## 텔레그램 메시지 형식 (AI 카페 자동 게시 연동용)

두 사이트 모두 상품명과 원본 링크만 담아 **동일한 텍스트 형식**으로 전송합니다.

```text
🆕 [HOTDEAL]

상품명: {상품명}

상품링크: {상품URL}
```

- 딜스팟: 새 상품의 이름과 해당 **딜스팟 상품 상세 링크**를 전송합니다.
- 네이버 카페: 제목이 `[ㅋㅍ]` 또는 `[ㅋㅍ프레시]`로 시작하는 새 글의 제목과 **카페 게시글 링크**를 전송합니다.
- 별도의 가격, 정상가, 할인율, 출처 필드는 넣지 않습니다. 단, 원본 제목 자체에 가격이나 판매처가 적혀 있으면 상품명에 포함될 수 있습니다.
- 모든 제목의 줄바꿈은 한 줄로 정리하고, 링크 미리보기는 꺼둡니다.
- 현재 구현된 기능은 **Telegram 알림 발송까지**이며, Telegram 메시지를 읽어 카페에 자동 게시하는 별도의 AI 연동은 아직 구현하지 않았습니다.

## DealSpot + Naver Cafe 감시 (자동 실행 중단)

- 단일 워크플로: `.github/workflows/hotdeal.yml` (**DealSpot + Naver Cafe new-item alerts**).
- **2026-11-01부터 GitHub Actions의 30분 예약 실행을 제거했습니다.** 이 두 사이트를 정기적으로 확인하거나 알림을 보내지 않습니다.
- 수동 점검용 `workflow_dispatch`만 남겨두었으며, 기존 저장된 감시 기록은 그대로 유지합니다.
- 뽐질(@jirum) 및 다른 별도 워크플로의 동작은 이번 변경에서 건드리지 않았습니다.
- Repository Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. 네이버 카페 감시에는 `NAVER_COOKIE`가 필요 없습니다.
- 한 번 실행할 때 두 스캐너를 별도의 단계로 실행합니다. 한쪽이 실패해도 나머지 스캔은 진행하며, 이미 보낸 메시지의 기록을 GitHub에 커밋합니다. 실패 사실은 작업 결과에 표시합니다.
- 읽기 전용 `preview=true` 실행은 `hotdeal-preview` 그룹으로 분리되어 정기 실행의 `hotdeal-state` 대기열을 막지 않습니다. 실제 전송 실행은 기존 기록 충돌 방지를 위해 `hotdeal-state`에서 순차 진행합니다.
- GitHub Actions 서비스 자체에서 비정상 대기 상태가 생기는 것까지 코드로 완전히 예방할 수는 없습니다.
- **무료 사용량:** 예약 실행 횟수가 2배로 늘지는 않지만, 합쳐진 작업의 실행 시간이 1분 단위 과금 경계를 넘으면 사용량이 증가할 수 있습니다.

## 수동 점검

1. Actions → **DealSpot + Naver Cafe new-item alerts** → **Run workflow**.
2. **preview=true** (기본값): 두 사이트를 조회만 하고 Telegram 발송 및 상태 기록을 하지 않습니다.
3. **preview=false**: 두 사이트의 신규 상품/글을 실제로 텔레그램에 보내고 상태를 저장합니다. 이미 기준 상태가 있으므로 신규 등록이 존재하면 바로 발송됩니다.
4. 로그에서 `DealSpot first-page scan`, `Naver Cafe first-page scan`, `Persist both alert histories` 순서로 확인합니다.

이전 `Naver Cafe new-post alerts (manual verification)`와 `Naver Cafe manual access probe` 워크플로는 스케줄이 없는 별도 점검용 파일이며, 자동 검사 횟수를 늘리지 않습니다.

## 뽐질(@jirum) 공개 핫딜 재전송 — 별도 5분 작업

- 워크플로: `.github/workflows/jirum-forward.yml` (**Jirum hotdeal repost to Telegram**).
- 5분마다 공개 채널 https://t.me/s/jirum 의 최근 글을 확인합니다. GitHub 서버 사정으로 실행이 늦어질 수 있습니다.
- 텔레그램 `forwardMessage` API는 봇의 채널 접근 권한 부족으로 실제 테스트에서 거절되어 사용하지 않습니다. 대신 공개 게시물의 본문과 **실제 링크 href**를 읽고, 기존 텔레그램 봇의 `sendMessage`로 새로운 메시지를 발송합니다.
- **원문에서 추출한 구매 링크를 변경·리다이렉트하지 않습니다.** 쿠팡 `link.coupang.com`, 토스 `toss.im` 등 판매처 구분 없이 실제 게시글의 외부 구매 URL을 전송합니다.
- AI 카페 게시 자동화와 호환되도록 다음 형식을 사용합니다:

```text
🆕 [HOTDEAL]

상품명: {뽐질 상품명}

상품링크: {게시글의 원본 구매 URL}

원문:
{뽐질 글 내용·가격·할인 및 제휴 안내}
```

- 원문 공개 본문만 텍스트로 재구성하므로 **뽐질 원본 사진, 이모티콘의 모양, 폰트/서식까지 동일하지는 않습니다.** 필요한 건 원본 상품 링크이며 그것을 가장 우선합니다.
- 예약 실행은 기존 Repository Variable `BOT_ENABLED=true`를 사용합니다. 기존 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` Secrets를 재사용합니다. 별도의 `JIRUM_ENABLED` 설정은 필요 없습니다.
- **첫 정상 실행 시 과거 글을 전송하지 않고 최신 게시글 번호만 기준으로 저장**합니다. 이후에는 새로 등록된 글만 순서대로 최대 30개씩 전송합니다. `verify-repost` 수동 테스트는 최신 글 1개를 전송하되 중복 방지 기록을 변경하지 않습니다. 실제 전송 성공한 뒤 ID를 기록하고, 실패 시 해당 글부터 다음 실행에 다시 시도합니다.
- 구매 링크가 없는 일반 공지글은 전송하지 않습니다. 모든 공개 글의 구매 링크가 파싱되지 않는다면 데이터 구조 변경 가능성이 있어 기록을 변경하지 않고 오류를 발생시킵니다.
- 수동 점검: Actions → Jirum hotdeal repost to Telegram → Run workflow → `preview` (조회만), `verify-repost` (최근 글 1개 전송, 기록 변경 없음), `run` (실제 자동 동작).
- 코드는 `test_jirum_forward.py`에 테스트하며, 별도 `validate-jirum.yml`에서 테스트와 공개 페이지 구매 링크 조회를 수행하되 텔레그램 메시지는 보내지 않습니다.
- 공개 웹 첫 페이지의 게시물만 확인하므로 글이 아주 빠르게 쌓이거나 GitHub가 오랫동안 지연되면 일부 누락 가능성이 있습니다.
- 기존 딜스팟·네이버 카페 30분 알림은 변경하지 않았습니다.

## 텔레그램 알림 개인 채팅 + 핫딜 MUSE용 그룹 동시 발송

- 딜스팟, 네이버 카페, 뽐질의 새 핫딜 알림은 `telegram_delivery.py` 공용 코드로 **개인 채팅과 확인된 '핫딜 MUSE용' 그룹**에 동일한 문구와 상품 링크를 순서대로 보냅니다.
- 개인 채팅은 기존 GitHub Actions Secret `TELEGRAM_CHAT_ID`와 `TELEGRAM_BOT_TOKEN`을 그대로 사용합니다.
- 그룹 채팅 ID는 워크플로 **Configure Telegram group delivery** (`.github/workflows/setup-telegram-group.yml`)에서 텔레그램 `getUpdates`로 탐색한 뒤 `getChat`에서 정확히 `핫딜 MUSE용`인지 재확인합니다.
- 그룹과 개인 채팅 각각 메시지 테스트가 성공해야 비공개 저장소 `state/telegram_group_chat.json`에 그룹 ID가 기록됩니다. 그룹 ID가 아직 없으면 기존 개인 알림만 전송합니다.
- 그룹이 검색되지 않으면 **'핫딜 MUSE용' 그룹 안에서 봇에게 `/chatid` 또는 `/chatid@봇아이디` 명령**을 보낸 다음 Actions → Configure Telegram group delivery → Run workflow를 다시 실행하세요. 개인방에서 보내면 그룹 ID를 알 수 없습니다.
- 봇이 그룹 대화를 읽을 권한은 필요 없으며, 다른 사람의 일반 대화를 수집하지 않습니다. 봇이 그룹에 메시지를 작성할 권한은 있어야 합니다.
- 그룹 전송이 실패하면 기존 성공 기록에 새 항목을 추가하지 않고 다음 검사에서 재시도합니다. 개인방 발송 후 그룹만 실패한 경우 개인방에는 일시적으로 같은 알림이 다시 올 수 있습니다.

## 쿠팡 파트너스 최종 승인 전 — 동일 상품 수동 링크 교체 준비

- `coupang_manual_links.py`가 `config/coupang_manual_links.json`을 읽고 뽐질(@jirum) 상품의 구매 URL을 선택적으로 교체할 수 있습니다.
- **현재 매핑은 비어 있습니다. 기존 텔레그램 메시지와 구매 링크를 전혀 변경하지 않습니다.**
- 본인의 파트너스 페이지에서 해당 상품/옵션에 대해 직접 발급한 링크가 있고, 뽐질 원문 링크도 완전히 같은 상품/옵션임을 확인한 경우에만 다음처럼 1:1 등록하세요:

```json
{
  "mappings": {
    "https://link.coupang.com/a/원본_상품_제휴링크": "https://link.coupang.com/a/동일상품_나의_제휴링크"
  }
}
```

- 원본 링크가 키와 완전히 일치하는 경우에만 교체하며, 다른 상품/링크 및 토스쇼핑 등은 변경하지 않습니다. 매핑 실수는 다른 상품의 광고로 이어지므로 게시 전 상품/옵션을 확인하세요.
- 변경된 링크로 전송할 때는 메시지 내 원래 구매 URL을 중복 노출하지 않습니다. 쿠팡 파트너스 경제적 이해관계 표시와 채널 등록·약관을 준수하세요.
- 최종 승인 뒤의 API 기반 자동 딥링크 생성은 **아직 구현되지 않았습니다.** API 키 발급 전에는 상품별 링크 자동 발급이 아닌 위 수동 대응만 가능합니다.
- 테스트: `python -m unittest -v test_coupang_manual_links.py test_jirum_forward.py` (텔레그램 발송 없음).

## 쿠팡 파트너스 간편 링크 브라우저 1건 테스트 (승인 전)

**실험용 별도 기능입니다. GitHub Actions에서 로그인하거나 실제 Telegram 알림을 바꾸지 않습니다.** 최종 승인 전 공식 API 키가 없을 때 사이트에서 직접 링크 생성이 가능한지 확인하는 용도입니다. 사이트의 현재 화면 구조나 정책에 따라 자동 입력/클릭이 실패할 수 있으며 그때는 사용자에게 수동 조작을 요청합니다.

1. PC에 Python 3.11 설치. 이 비공개 저장소 코드를 로컬 디렉터리에 내려받습니다.
2. `python -m pip install playwright` 및 `python -m playwright install chromium`으로 독립 브라우저를 설치합니다.
3. 쿠팡의 **실제 상품 상세 페이지 URL**을 준비합니다. 반드시 `https://www.coupang.com/vp/products/...?...itemId=...&vendorItemId=...` 형태여야 합니다. 다른 사람의 `link.coupang.com` 제휴 단축 URL을 그대로 넣지 마세요.
4. 실행: `python coupang_browser_probe.py --product-url "https://www.coupang.com/vp/products/123456789?itemId=111&vendorItemId=222"` (숫자는 **실제 상품 값**으로 바꿉니다).
5. **표시되는 별도 Playwright 브라우저에서 직접 로그인**하고 `링크 생성 > 간편 링크 만들기`로 이동, CMD에서 Enter를 누릅니다. 프로그램이 입력칸/버튼을 유일하게 확인하면 자동 조작하고, 인식할 수 없으면 수동 작업을 요구합니다.
6. 성공하면 생성된 `https://link.coupang.com/a/...` 링크를 로컬 콘솔에 보여줍니다. 상품·옵션·수량·판매자를 직접 비교하고, 동일한 상품임이 확인되기 전에는 어떤 다른 링크도 교체하지 마세요.

- 플레이wright 브라우저의 로그인 쿠키는 **로컬의 `.coupang_partner_profile/`에만 보관**하고 Git에 올리지 않습니다. 이 프로필은 일반 Chrome의 프로필과 별개입니다.
- 자동 봇 발송/실제 제휴 링크 매핑/검색 자동화/광고 클릭은 **하지 않습니다**. 비정상 접속 제한·보안 확인이 나타나면 우회하지 말고 중단하세요.
- 이 시험은 로그인된 브라우저에서 1회 작동을 확인하는 **실험**입니다. GitHub Actions 같은 무인 서버에 세션을 복사하는 것은 별도 보안·약관 검토 없이 진행하면 안 됩니다.
- 정책 위반 가능성이 우려되면 쿠팡 파트너스 고객지원에 브라우저 자동 링크 생성의 허용 여부를 먼저 문의하세요.

### 뽐질 링크 `https://link.coupang.com/a/...`를 직접 입력하는 실험

이제 테스트 프로그램은 **원본 단축 제휴 링크**를 1개 받을 수 있습니다. PC용 CMD에서 최신 `coupang_browser_probe.py`를 내려받아 다음처럼 실행하세요.

```bat
cd /d C:\Users\User\hotdeal-live
python coupang_browser_probe.py --source-url "https://link.coupang.com/a/ihyVQ9K1qC"
```

- 진행 동의(`YES`) 후 별도 일회용 Playwright 브라우저에서 원본 단축 링크를 **한 번만** 엽니다. 자동으로 파트너스 계정에 로그인하거나 봇에 전송하지 않습니다. 원본 제휴 링크를 여는 것은 원래 발행자에게 클릭 기록을 남길 수 있어 대량으로 반복 조회하지 마세요.
- 연결된 주소가 쿠팡 상세 상품 페이지이고 `productId`, `itemId`, `vendorItemId`를 모두 확인할 수 있어야만 진행합니다. `lptag`, `subid`, 기타 원 발행자의 추천·추적 파라미터를 제거한 **실제 동일 옵션 상품 주소**를 만듭니다.
- 확인된 상세 URL만 간편 링크 만들기 화면에 전달해 본인 계정의 새 제휴 링크 생성을 시도합니다. 쿠팡 로그인과 사람 확인(CAPTCHA)이 필요하면 사용자가 직접 진행합니다.
- 리다이렉트가 앱으로 이동하거나 옵션 ID가 확인되지 않으면 다른 상품을 추정하지 않고 **실패 종료**합니다. 이때 해당 상품 주소를 직접 확인한 후 기존 `--product-url` 옵션으로 다시 시도하세요.
- 생성 URL의 구문 검증과 판매 옵션의 실제 일치 검증은 별도입니다. **전송 전 상품명·수량·옵션·판매자를 사람이 확인해야 합니다.**
- 승인 전 정식 API 링크 발급 자동화나 GitHub 무인 운영 기능은 아직 없습니다. 이 프로그램은 로컬 1건 실험 전용이며, 기존 Telegram 메시지·파트너스 매핑은 변경하지 않습니다.
