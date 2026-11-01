# 당근 카페 GitHub 로그인 세션 · 글쓰기 화면 통합 테스트

상태: 수동 GitHub Actions 1회 드라이런 전용. 글 발행/자동 스케줄 없음.

## 전체 흐름

1. 기존 뽐질 자동화가 최신 쿠팡 상품 하나의 제목·특가·로우차트 관측 평균·카카오 OG 상품 사진·사용자 AFFSDP 미발급 실험 링크를 만든다.
2. GitHub Secret의 당근 브라우저 로그인 상태를 로드한다.
3. 사용자 본인 카페의 정확한 URL에 접속하여 글쓰기 버튼을 찾는다.
4. "오늘의 핫딜" 게시판 컨트롤을 발견하면 선택하고 제목·본문·사진을 채운다.
5. **발행/등록 버튼은 클릭하지 않는다.** 브라우저 종료 후 입력한 글은 보존되지 않을 수 있다.
6. Actions Artifact에 이미지, 문구, 상태 JSON, 편집창 스크린샷을 1일 보관한다. 세션 쿠키나 JSON은 아티팩트에 저장하지 않는다.

## 사전 준비 — 본인 당근 로그인 세션 내보내기 (1회)

GitHub Actions의 임시 브라우저는 개인 PC나 Muse 서버에 로그인된 Chrome 세션을 자동 공유하지 못한다.
로컬 PC에서 저장소를 내려받아 다음 명령을 실행한다.

    python -m pip install playwright==1.64.1
    python -m playwright install chromium
    python tools/daangn_export_session.py --cafe-url "https://cafe.daangn.com/"

열린 브라우저에서 본인 당근 계정으로 직접 로그인하고 본인 카페에서 글쓰기 버튼이 보이는 것을 확인한 뒤 터미널에 Enter를 입력한다.

기본 결과 파일은 사용자 홈 폴더의 daangn_cafe_storage_state.json. 이 파일은 로그인 쿠키를 담을 수 있다. Git에 커밋하거나 채팅에 올리지 않는다.

GitHub 저장소 mw5114-tech/hotdeal-bot → Settings → Secrets and variables → Actions → New repository secret에서:

- Name: DAANGN_CAFE_STORAGE_STATE_JSON
- Secret: 로컬 daangn_cafe_storage_state.json 파일의 전체 JSON

기존 카카오 로그인 Secret KAKAO_DEV_STORAGE_STATE_JSON도 필요하다. 유효기간이 지나면 사진 확보가 실패할 수 있다.

## GitHub 수동 테스트 실행

GitHub Actions → "Daangn Cafe session editor photo-price draft (manual, NO PUBLISH)" → Run workflow.

- cafe_url: 정확한 본인 카페 주소, 예: https://cafe.daangn.com/own-cafe-slug
- board_name: 기본값 "오늘의 핫딜" (실제 카페 게시판명을 적용)
- 결과: daangn-manual-editor-dry-run 아티팩트 내 status.json, 스크린샷, captions.txt, drafts.json, JPG.

상태 "EDITOR_INPUTS_FILLED_DRY_RUN"은 제목·본문이 브라우저 에디터 화면에 입력되었다는 뜻이지 게시/서버 저장 성공은 아니다.
"image_attached"는 파일 입력 이벤트 성공 여부만 나타낸다. 별도 저장 완료 확인이 필요한 편집기는 후속 실전 UI 테스트에서 확인해야 한다.

## 제한 및 가드

- AFFSDP 실험 주소는 같은 쿠팡 상품으로 리다이렉트되는 것으로 관측되었지만 공식 발급 여부, 클릭 귀속, 구매 수수료는 확인되지 않았다. 이를 수익 인증 링크로 표현하지 않는다.
- 로우차트 관측 평균은 쿠팡 공식 정상가나 최근 30일 평균이 아니다.
- 본문에 쿠팡 파트너스 경제적 이해관계 표시를 추가한다.
- 당근 세션은 GitHub Secret에서만 읽고, 만료되거나 기기 확인을 요구하면 중단한다.
- cafe_url은 정확한 사용자 소유 카페 URL이어야 한다. 다른 카페에 로그인해서 무단으로 글을 올리지 않는다.
- 기존 DealSpot/Naver GitHub 30분 감시 중단 상태, Jirum GitHub 감시 수동 상태, 클라우드 텔레그램 감시기, 카페 게시물은 변경하지 않는다.
