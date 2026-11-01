# 뽐질 → 당근카페 수동 FIFO (1회 1글)

## 작동 원칙
- 자동 schedule 없음. 직접 GitHub Actions Run workflow를 누르거나, 연결된 GitHub 도구로 정확한 수동 트리거 파일을 수정했을 때만 1회 실행.
- 워크플로 이름: **MANUAL | Jirum Coupang FIFO to Daangn (ONE post)**
- 94743번보다 큰 새로운 메시지만 처리. 이미 올린 94737·94738·94743 제외.
- 뽐질 @jirum 최신 글 및 필요한 이전 페이지를 조회. 쿠팡 상품/단축 링크만 필터링. 다른 쇼핑몰 상품은 무시.
- 메시지 ID가 작은 대기 상품부터 단 한 개 발행. 여러 개 쌓였으면 다음 클릭에 다음 상품 발행.
- 새 쿠팡 상품도 대기 글도 없으면 NO_NEW_COUPANG_DEAL을 기록하고 글을 쓰지 않음.
- 영구 상태 파일: state/daangn_jirum_manual_fifo.json (main 브랜치). 스캔 위치, 대기열, 발행 기록, 진행 중 기록 저장.
- concurrency로 중첩 실행 직렬화. 저장 충돌 시 덮어쓰지 않음.
- 발행 전에 CLAIMED 저장. 사진 먼저 업로드하고 본문 입력. 단 한 번만 등록 클릭. 완료 페이지에서 링크 전체, 사진, 글 제목, 게시판, 고지 문구 검증.
- 결과가 모호하거나 작업 중단 시 REVIEW_REQUIRED로 잠그고 자동 재시도 금지. 카페에서 사실 확인 후 수동으로 해제해야 함.

## GitHub Actions 직접 실행
1. 저장소 mw5114-tech/hotdeal-bot → Actions 탭
2. **MANUAL | Jirum Coupang FIFO to Daangn (ONE post)** 선택
3. Run workflow → main → Run workflow 클릭

## 새 채팅에서 GitHub 도구로 딸깍 실행
- main 브랜치의 **manual/trigger-daangn-jirum.txt**를 변경한다.
- 파일이 없다면 최초 클릭에 create_file, 이후 클릭에서는 fetch_file로 현재 SHA 조회한 뒤 update_file.
- 실행 번호나 고유 요청 문구를 이전과 다른 내용으로 반드시 변경해야 새로운 push가 발생한다.
- 위 정확한 경로만 push 트리거 조건이고, 배포 PR에는 트리거 파일을 포함하지 않는다. 그래서 병합 시 자동으로 시작되지 않는다.
- 채팅에 말만 해서는 실행되지 않는다. GitHub 도구의 파일 생성/갱신 성공이 필요하다.

## 결과
- READY_ONE: 큐에 상품 있어 가장 오래된 1개 발행 시도
- NO_NEW_COUPANG_DEAL: 새 쿠팡 상품 없음, 발행 0개
- POST_PUBLISHED_AND_FORMAT_VERIFIED: 발행 및 사진·링크·문구 검증 성공, 영구 완료 처리
- REVIEW_REQUIRED: 발행 여부 불확실, 중복 방지를 위해 잠금
- TELEGRAM_HISTORY_INCOMPLETE_NO_WATERMARK_ADVANCE: 이전 글 확인 실패, 진행 위치 변경하지 않고 중단

## 전제 및 참고
- 사진·가격·상품 옵션 또는 본인 태그 검증 실패 시 등록하지 않음.
- 원본 텔레그램 할인 정보와 현재 판매 가격이 항상 일치하지는 않음.
- 계산한 AFFSDP 링크는 사용자의 태그가 붙은 후보이며 쿠팡 공식 발급·수수료 귀속까지 확인된 링크는 아님.
- 평소 판매가로 표기하는 참고 가격은 로우차트 공개 관측 평균으로, 쿠팡 공식 정상가는 아님.
- 기존 텔레그램 전달 봇, 다른 GitHub 자동화, 클라우드 워커는 변경하지 않음.
