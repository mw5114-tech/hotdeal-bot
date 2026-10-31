# 뽐질 → 내 쿠팡파트너스 링크 → 당근 카페

## 확인된 대상

- 저장소: mw5114-tech/hotdeal-bot (비공개)
- 원본: https://t.me/s/jirum (로그인 없는 공개 미리보기 수집 확인)
- 카페: https://cafe.daangn.com/i-gagyeog-mos-c
- 게시판: 🔥 오늘의 핫딜, URL 끝 QdYG0O2A
- 작성: https://cafe.daangn.com/i-gagyeog-mos-c/posts/new
- 현재 연결 브라우저에서 카페 관리 및 글쓰기 화면 확인.
- 작성 폼에는 게시판 선택, 제목, 본문, 이미지, 공개 범위가 있음.
- 현재 쿠팡파트너스 연결 브라우저는 로그아웃 상태. API 키는 사용자 진술상 없음.
- 브라우저에서 글쓰기 가능하다는 사실은 GitHub runner의 로그인 가능성을 보장하지 않음.

## 이번 추가 기능

GitHub Actions에서 공개 채널을 한 번 읽고 쿠팡 상품만 검토용 초안으로 변환.
본문에는 원본 제휴 링크를 넣지 않음. 본인 링크 자리에는 확인용 문구만 표시.
가격은 원문 표시 가격으로 명시. 사진·실시간 가격·옵션 일치를 추정하지 않음.
모든 초안은 BLOCKED, publish_ready=false. 실제 게시 코드와 예약 실행은 없음.

- 워크플로: Daangn review drafts (no publishing)
- Artifact: daangn-review-drafts / drafts.txt, drafts.json
- 구문이 유사한 가짜 쿠팡 도메인 제외
- 가격 머리말을 상품명으로 잘못 선택하는 경우를 초안 작성 단계에서 처리
- 테스트: python -m unittest -v gh_only.test_pipeline gh_only.test_partner_api gh_only.test_mtproto_reader gh_only.test_daangn_drafts

## 다음 시험 순서

1. 사용자가 파트너스에 직접 로그인.
2. 동일 상품·옵션의 본인 링크 한 건 발급 가능 여부 확인.
3. API 없이 생성하려면 로그인된 웹 간편 링크 화면의 실제 동작과 세션 유지 확인이 필요.
4. GitHub runner에서 인증 재사용을 별도로 검증. 개인 PC 브라우저의 로그인은 자동 이전되지 않음.
5. 소량 게시 시험 후 게시 주소와 본문을 읽어 결과 확인.
6. 지속 운영은 영구 중복 기록, 만료된 인증 감지, 게시 결과 불명확 시 자동 재발행 금지, 수집 누락 회수 구현 후 연결.

GitHub 연결 권한과 쿠팡/당근 인증은 별개이다. API 키 또는 적합한 로그인 경로 없이 링크 자동 발급 완료라고 표시하면 안 된다.
기존 다른 알림 워크플로나 외부 서버는 이번 변경으로 전환/중지되지 않는다.
