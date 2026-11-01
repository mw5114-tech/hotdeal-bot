# 뽐질 → 카카오 OG 사진 → 로우차트 관측 평균 → 내 AFFSDP 실험 URL

이것은 기존 `test/github-only-pipeline-20261031` 브랜치의 실제 성공한 사진·가격
초안 생성 기능을 메인 브랜치에 포팅하고, **발급되지 않은 AFFSDP 실험 주소**
조합을 마지막 단계에 추가한 독립 실행 실험이다.

## 사용법

GitHub 저장소 → Actions → **Jirum Kakao photo + Lowchart price + owner AFFSDP draft (manual)**
→ Run workflow → 실행 완료 후 `jirum-photos-prices-affsdp-candidate` 아티팩트 확인.

- `captions.txt`: 상품명 / 로우차트 관측 평균가 / 뽐질 글에 게시된 특가 /
  **AFFSDP 실험 주소** 순으로 작성된 문구.
- `drafts.json`: 원본 상품 링크, 실측 아이템 ID, 가격 근거, 사진 메타,
  조합된 URL, 발급 여부 및 수익 귀속 미검증 상태.
- `jirum_<id>_kakao_og.jpg`: 카카오 OG가 동일 상품으로 확인되고
  쿠팡 CDN에서 다운로드가 성공했을 때만 저장.

현재 `COUPANG_PARTNER_LPTAG=AF9668915`을 **이 수동 테스트 워크플로 안에서만**
사용한다. GitHub Actions Secret `KAKAO_DEV_STORAGE_STATE_JSON`은
기존 브랜치에서 사용하던 카카오 개발자 계정 로그인 브라우저 상태다.
만료되거나 없으면 카카오 OG/사진이 조회되지 않을 수 있다.

## 링크 조합의 엄격한 범위

- 원본이 쿠팡 `/a/` 단축 링크라면 먼저 **1회의 직접 리다이렉트**를 조회한다.
  상품 `productId`와 `itemId`를 얻지 못하면 링크를 만들지 않는다.
- `https://link.coupang.com/re/AFFSDP?lptag=AF9668915&pageKey=...&itemId=...`
  형식을 구성한다. 원본에서 `vendorItemId`가 확인된 경우에만 추가한다.
  다른 제휴자의 `lptag`, `traceid`, `subid`를 전달하지 않는다.
- 조합 URL이 같은 상품을 열도록 리다이렉트되는 것은 별도 실험에서 확인됐지만
  **쿠팡 파트너스가 이 주소를 공식 발급하거나 네 계정의 클릭/구매 실적을
  인정한다는 증거는 없다.** 단순 형식 생성 테스트를 수익 성공으로 표현하지 않는다.
- 생성 문구도 `상품 링크 (수익 귀속 미검증)`으로 표시하며,
  결과 JSON의 `official_link_issued=false`,
  `commission_attribution_verified=false`,
  `ready_for_automatic_publication=false`를 유지한다.

## 가격과 이미지

- 뽐질 메시지에서 판매가를 읽는다. *현재 쿠팡 실제 판매가*로 주장하지 않는다.
- 로우차트의 동일 상품/itemId 관측 **전체 이력 평균**을 가져온다.
  쿠팡 공식 정상가는 아니며, 30일 평균도 아니다.
- 패키지 옵션 또는 제목이 일치하지 않으면 관측 평균 및 할인율을 숨긴다.
- 사진은 카카오 개발자 공유 디버거의 OG 정보에서 쿠팡 이미지 CDN 주소를
  가져와 검증한 경우에만 다운로드한다. 이것은 공식 카카오 REST API가 아닌
  로그인된 브라우저 UI 실험이다.

## 기존 자동화는 보존

이 PR은 **새 수동 워크플로만 추가**한다.
`.github/workflows/hotdeal.yml` (딜스팟·네이버카페 30분 예약 중단 상태),
`.github/workflows/jirum-forward.yml` (뽐질 GitHub 자동 감시 중단 상태),
기존 텔레그램 발송 코드, 실제 클라우드 감시기, 카페 발행 코드,
기존 기록/상태 파일은 **수정하지 않는다**.

현재 결과는 사진과 가격 및 후보 URL이 담긴 **초안 아티팩트 생성**까지이며,
실제 텔레그램이나 당근 카페에 발행하지 않는다.
