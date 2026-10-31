"use strict";

const QUICK_LINK_PAGE = "https://partners.coupang.com/#affiliate/ws/link-to-any-page";
const $ = (id) => document.getElementById(id);

function canonicalProductURL(raw) {
  if (typeof raw !== "string" || raw.length > 3000) throw new Error("상품 URL이 너무 길거나 비어 있음");
  const url = new URL(raw.trim());
  if (url.protocol !== "https:" || !["coupang.com", "www.coupang.com"].includes(url.hostname) ||
      url.username || url.password || url.hash || (url.port && url.port !== "443")) {
    throw new Error("일반 쿠팡 HTTPS 상품 URL만 입력 가능");
  }
  const m = url.pathname.match(/^\/vp\/products\/(\d+)\/?$/);
  if (!m) throw new Error("쿠팡 상품 상세 URL만 가능. 뽐질 단축링크는 GitHub에서 일반 URL로 변환해 사용");
  const itemIds = url.searchParams.getAll("itemId");
  const vendorIds = url.searchParams.getAll("vendorItemId");
  if (itemIds.length !== 1 || !/^\d+$/.test(itemIds[0]) ||
      vendorIds.length > 1 || (vendorIds.length === 1 && !/^\d+$/.test(vendorIds[0]))) {
    throw new Error("정확한 itemId가 한 개 필요하고 vendorItemId는 선택사항");
  }
  return "https://www.coupang.com/vp/products/" + m[1] +
    "?itemId=" + itemIds[0] +
    (vendorIds.length ? "&vendorItemId=" + vendorIds[0] : "");
}

function show(message, isError = false) {
  $("status").textContent = message;
  $("status").style.color = isError ? "#a51e24" : "#172334";
}

async function currentPartnerTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  const u = tab?.url ? new URL(tab.url) : null;
  if (!tab?.id || !u || u.protocol !== "https:" || u.hostname !== "partners.coupang.com") {
    throw new Error("일반 크롬에서 로그인된 partners.coupang.com 탭을 열고 다시 시도해");
  }
  return tab.id;
}

async function runOnPartner(fn, args = []) {
  const tabId = await currentPartnerTab();
  const result = await chrome.scripting.executeScript({target: {tabId}, func: fn, args});
  return result?.[0]?.result ?? {status:"NO_RESULT"};
}

function isVisible(node) {
  return !!(node && node.getClientRects().length &&
            getComputedStyle(node).visibility !== "hidden");
}

function inputInPage(url) {
  // Runs only on user click in the already signed-in, normal Chrome tab.
  const nodes = Array.from(document.querySelectorAll("input, textarea"))
    .filter(el => el.tagName === "TEXTAREA" || !["hidden","password","email","tel","number"].includes(el.type))
    .filter(el => el.getClientRects().length && !el.disabled);
  const ranked = nodes.map(el => {
    const details = [el.getAttribute("placeholder"),el.getAttribute("aria-label"),
                     el.getAttribute("name"),el.getAttribute("id"),
                     el.closest("label")?.textContent].filter(Boolean).join(" ");
    let score = 0;
    if (/쿠팡\s*URL|쿠팡\s*링크|쿠팡\s*주소/i.test(details)) score += 10;
    if (/상품\s*URL|상품\s*링크|상품\s*주소/i.test(details)) score += 6;
    if (/\bURL\b|주소/i.test(details)) score += 2;
    return {el,score,details:details.slice(0,65)};
  }).sort((a,b)=>b.score-a.score);
  if (!ranked.length || ranked[0].score < 5 ||
      (ranked.length > 1 && ranked[0].score === ranked[1].score)) {
    return {status:"INPUT_UNCONFIRMED", visibleInputCount:nodes.length,
            candidates:ranked.slice(0,5).map(x=>x.details)};
  }
  const input = ranked[0].el;
  const descriptor = Object.getOwnPropertyDescriptor(
    input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
    "value"
  );
  if (!descriptor?.set) return {status:"NATIVE_SETTER_UNAVAILABLE"};
  descriptor.set.call(input,url);
  input.dispatchEvent(new Event("input",{bubbles:true}));
  input.dispatchEvent(new Event("change",{bubbles:true}));
  input.focus();
  return {status:"FILLED",label:ranked[0].details};
}

function generateInPage() {
  const buttons = Array.from(document.querySelectorAll("button, input[type=button], input[type=submit]"))
    .filter(el=>el.getClientRects().length && !el.disabled);
  const matches = buttons.filter(el =>
    (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().replace(/\s+/g," ") === "링크 생성");
  if (matches.length !== 1) {
    return {status:"GENERATE_BUTTON_UNCONFIRMED",candidateCount:matches.length,
            visibleButtonLabels:buttons.slice(0,15).map(b=>(b.innerText||b.value||"").trim().slice(0,35))};
  }
  matches[0].click();
  return {status:"GENERATE_CLICKED",linkVerified:false};
}

function readGeneratedLinkInPage() {
  const allowed = /^https:\/\/(?:link\.coupang\.com\/a\/[A-Za-z0-9_-]+|coupa\.ng\/[A-Za-z0-9_-]+)\/?$/;
  const candidates = [];
  const elements = Array.from(document.querySelectorAll("input,textarea,a[href]"));
  for (const element of elements) {
    if (!element.getClientRects().length) continue;
    for (const value of [element.value,element.getAttribute?.("href")]) {
      const maybe = typeof value === "string" ? value.trim() : "";
      if (allowed.test(maybe) && !candidates.includes(maybe)) candidates.push(maybe);
    }
  }
  return {status:candidates.length === 1?"ONE_LINK_FOUND":
    candidates.length === 0?"LINK_NOT_FOUND":"MULTIPLE_LINKS_REVIEW_REQUIRED",
    candidates:candidates.slice(0,5)};
}

async function execute(action) {
  try {
    $("answer").hidden = true;
    if (action === "open") {
      const canonical = canonicalProductURL($("source").value);
      await chrome.storage.local.set({pendingProductURL:canonical});
      await chrome.tabs.create({url:QUICK_LINK_PAGE});
      show("간편 링크 탭을 열었음. 기존 쿠팡 로그인 상태인지 확인한 다음 이 확장 프로그램을 다시 열어 ② 진행.");
      return;
    }
    if (action === "fill") {
      const url = canonicalProductURL($("source").value);
      await chrome.storage.local.set({pendingProductURL:url});
      const r = await runOnPartner(inputInPage,[url]);
      show(r.status === "FILLED" ? "상품 URL 입력 성공. 사이트 화면에 URL이 맞는지 확인하고 ③ 버튼을 눌러."
        : "입력칸 식별 안 됨: " + JSON.stringify(r),r.status !== "FILLED");
      return;
    }
    if (action === "generate") {
      const r=await runOnPartner(generateInPage);
      show(r.status === "GENERATE_CLICKED" ?
        "공식 '링크 생성' 버튼 클릭함. 페이지에서 결과가 보이면 ④ 눌러. 아직 내 계정 링크라고 검증된 것은 아님."
        : "생성 버튼 식별 안 됨: " + JSON.stringify(r),r.status !== "GENERATE_CLICKED");
      return;
    }
    if (action === "extract") {
      const r=await runOnPartner(readGeneratedLinkInPage);
      if (r.status === "ONE_LINK_FOUND") {
        $("result").value = r.candidates[0];
        $("answer").hidden = false;
        show("한 개의 링크를 찾았어. 쿠팡 파트너스 화면과 목적 상품을 확인한 뒤 복사해.");
      } else {
        show("링크 확정 불가: "+JSON.stringify(r),true);
      }
    }
  } catch(error) {
    show(String(error?.message || error), true);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  const state=await chrome.storage.local.get("pendingProductURL");
  if (state.pendingProductURL) $("source").value=state.pendingProductURL;
  for (const action of ["open","fill","generate","extract"]) {
    $(action).addEventListener("click",()=>execute(action));
  }
  $("copy").addEventListener("click",async()=>{
    try{await navigator.clipboard.writeText($("result").value);show("클립보드 복사됨. 내 계정에서 만든 링크인지 한 번 확인해.");}
    catch(e){show("복사 실패: 결과 창에서 링크를 직접 선택해 복사해.",true);}
  });
});
