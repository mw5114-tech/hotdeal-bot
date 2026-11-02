# 2026-11-02 당근카페 FIFO 중단: 카카오 로그인 만료

## 원인 — GitHub Actions 실측

- 실패 실행: https://github.com/mw5114-tech/hotdeal-bot/actions/runs/41381981112
- 진단 아티팩트: `jirum-fifo-drain-41381981112/drafts.json`
- 첫 미발행 소스 ID: `94789`
- `product_identity.status = EXACT_ITEM_IDENTIFIED` (Coupang item ID 추출 정상)
- `kakao_status = KAKAO_LOGIN_EXPIRED`
- `photo.status = NO_MATCHING_KAKAO_IMAGE`, `kakao_product_match = false`
- `POST_EVIDENCE_NOT_READY`: 사진·상품 일치 검증이 안 되어 게시 전에 중단
- `in_flight = null`: 게시 버튼 클릭/글 발행은 발생하지 않았음
- 최신 확인 시점: 대기 46개. 기존 발행 기록은 보존

이는 GitHub Actions 실행 권한, Telegram 감시, Oracle API 장애와 다른 문제다.
**Kakao Developers 로그인 세션을 갱신해야 실제 사진이 있는 글을 다시 올릴 수 있다.**

## 자동 중단 (회로 차단) 동작

첫 확인 시 `state/daangn_jirum_manual_fifo.json`의
`kakao_auth_block`에 **상태/메시지 ID/검출 시각/비밀정보가 아닌 SHA-256 지문만** 저장한다.

다음 5분 예약 실행은 새 Telegram 메시지의 대기열 기록을 계속 유지하면서,
기존 세션 지문이 같으면 브라우저 설치·Kakao 이미지 조회·카페 게시를
전부 건너뛴다. GitHub Actions에는 `KAKAO_REAUTH_REQUIRED`라고 표시됨.

GitHub Secret을 새 값으로 갱신하면 지문이 달라지고, 다음 예약 실행에서
`kakao_auth_block`이 자동으로 해제된다. 첫 사진·상품 확인이 여전히
실패하면 별개 진단으로 안전하게 중단한다.

**중요:** 새 Secret을 넣기만 하면 밀린 글을 FIFO로 전부 게시하려고 한다.
핫딜은 오래되면 가격이 달라지거나 품절될 수 있으므로, 재개 전에
대기 중인 오래된 상품을 별도로 검토하는 것을 권장한다.
발행/대기 기록을 임의로 삭제하거나 전체 초기화하지 않는다.

## 세션 갱신 (사용자 PC에서만)

로컬 Chrome 브라우저, Python/Playwright 및 로그인된 GitHub CLI가 필요하다.
`KAKAO_DEV_STORAGE_STATE_JSON`을 채팅이나 GitHub 레포 파일에 붙이지 않는다.

```bash
# 본인 PC의 저장소 체크아웃에서
gh auth status
python -m pip install playwright
python -m tools.refresh_kakao_storage_secret
```

크롬이 열리면 Kakao Developers에 직접 로그인한 뒤
`https://developers.kakao.com/tool/debugger/sharing` 페이지까지 이동해서
터미널에서 Enter를 누른다. 프로그램이 Kakao/Daum 도메인의
스토리지 상태만 필터링하여, 임시 JSON 파일을 남기거나 로그에 보이지
않도록 `gh secret set KAKAO_DEV_STORAGE_STATE_JSON --repo mw5114-tech/hotdeal-bot`
명령의 표준 입력으로 전송한다.

카카오 측에서 사용자 확인, 추가 인증 또는 쿠키 거부가 나타나면
프로그램으로 우회하지 말고 본인 브라우저에서 직접 인증한다.

## 수동 검증

GitHub 저장소의 `Actions` → `Jirum Coupang | Daangn FIFO drain (5min + manual)`
에서 `Run workflow`를 실행할 수 있다.

- 확인만 하려면 `smoke_test=true`: GitHub 연결 검사만 진행, 게시 안 함
- 정상 운영 `smoke_test=false`: **실제 상품 게시 작업 실행**
- 상태파일의 `kakao_auth_block` 제거 및 새 `JIRUM_MANUAL_PUBLISH` 로그 확인
- 오류가 나면 최신 아티팩트 `drafts.json`의 `kakao_status`, `photo.status` 확인

예전 5분 실행의 오류 메일은 해당 시점의 실패 기록이다.
새 세션이 적용되기 전까지 브라우저 재시도 비용만 아끼도록 중단된다.
