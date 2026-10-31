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
    // Real Coupang's single product URL field is labelled simply "url".
    if (/쿠팡\s*URL|쿠팡\s*링크|쿠팡\s*주소/i.test(details)) score += 12;
    if (/상품\s*URL|상품\s*링크|상품\s*주소/i.test(details)) score += 10;
    if (/\burl\b|주소/i.test(details)) score += 8;
    if (/coupang|producturl|product-url/i.test(details)) score += 6;
    return {el,score,details:details.slice(0,65)};
  }).sort((a,b)=>b.score-a.score);
  // A future renamed field is safe only if the ONE editable input is under
  // a visible form labelled "쿠팡 URL". Do not guess between multiple fields.
  const formLabel = Array.from(document.querySelectorAll("label, h1, h2, h3, h4"))
    .filter(el => el.getClientRects().length > 0)
    .some(el => /쿠팡\s*URL/i.test(el.innerText || el.textContent || ""));
  const onlyInputOnKnownForm = ranked.length === 1 && formLabel;
  if (!ranked.length || (ranked[0].score < 5 && !onlyInputOnKnownForm) ||
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
  if ((input.value || "").trim() !== url) {
    return {status:"INPUT_VALUE_NOT_SET"};
  }
  return {status:"FILLED",label:ranked[0].details,
          exactValueConfirmed:true};
}

function generateInPage(expectedProductURL) {
  // Self-contained: chrome.scripting serializes this function into the tab.
  // Require the exact expected plain Coupang URL to be visible in the page.
  const visible = el => !!(el && el.getClientRects().length && !el.disabled &&
    getComputedStyle(el).visibility !== "hidden");
  const fields = Array.from(document.querySelectorAll("input, textarea"))
    .filter(visible)
    .filter(el => (el.value || "").trim() === expectedProductURL);
  if (fields.length !== 1) {
    return {status:"PRODUCT_URL_NOT_IN_PAGE", matchingFields:fields.length};
  }

  // Coupang currently renders a blue "링크 생성" button. In its DOM the visible
  // label may contain invisible chars, SVG text, nested spans, or accessibility
  // labels, even if it looks exactly like "링크 생성" in the browser.
  const clean = value => String(value ?? "").normalize("NFKC")
    .replace(/[\u200B-\u200F\u2060\uFEFF]/g,"")
    .replace(/[\s\u00A0]+/g,"")
    .trim();
  const textOptions = el => [el.innerText,el.textContent,el.value,
                            el.getAttribute("aria-label"),el.getAttribute("title")];
  const isCreateLabel = value => {
    const t=clean(value);
    return t === "링크생성" || (t.startsWith("링크생성") && t.length <= 18);
  };
  // Prefer real buttons over their clickable wrapper so one user click means
  // exactly one click on the target. Never blindly click the first button.
  const native = Array.from(document.querySelectorAll(
    "button, input[type=button], input[type=submit]"
  )).filter(visible);
  let matches = native.filter(el=>textOptions(el).some(isCreateLabel));
  if (matches.length === 0) {
    const roleButtons = Array.from(document.querySelectorAll("[role=button]"))
      .filter(visible);
    matches=roleButtons.filter(el=>textOptions(el).some(isCreateLabel));
  }
  if (matches.length !== 1) {
    const relevant=native.filter(el=>textOptions(el).some(x=>clean(x).includes("링크")));
    return {
      status:"GENERATE_BUTTON_UNCONFIRMED",
      candidateCount:matches.length,
      visibleButtonCount:native.length,
      matchingFieldCount:fields.length,
      // Short public UI label diagnostic only, never page HTML, cookies or account data.
      relevantButtonLabels:relevant.slice(0,5).map(el=>({
        tag:el.tagName,
        innerText:clean(el.innerText).slice(0,45),
        textContent:clean(el.textContent).slice(0,45),
        aria:clean(el.getAttribute("aria-label")).slice(0,45),
      }))
    };
  }

  matches[0].click();
  return {status:"GENERATE_CLICKED",linkVerified:false,buttonTag:matches[0].tagName};
}

function readGeneratedLinkInPage() {
  // Inspect only visible user-facing link results on the official site. Never
  // capture full page text in logs or any browser auth state.
  const allowed = /^https:\/\/(?:link\.coupang\.com\/a\/[A-Za-z0-9_-]+|coupa\.ng\/[A-Za-z0-9_-]+)\/?$/;
  const findLinks = /https:\/\/(?:link\.coupang\.com\/a\/[A-Za-z0-9_-]+|coupa\.ng\/[A-Za-z0-9_-]+)/g;
  const candidates = [];
  const add = raw => {
    if (typeof raw !== "string") return;
    const text=raw.trim();
    if (allowed.test(text) && !candidates.includes(text)) candidates.push(text);
    const matches=text.match(findLinks) || [];
    for (const m of matches) if (allowed.test(m) && !candidates.includes(m)) candidates.push(m);
  };
  const elements = Array.from(document.querySelectorAll(
    "input, textarea, a[href], [data-clipboard-text], [data-clipboard-target], [role=textbox]"
  ));
  for (const el of elements) {
    if (!el.getClientRects().length) continue;
    add(el.value);
    add(el.getAttribute("href"));
    add(el.getAttribute("data-clipboard-text"));
    add(el.innerText);
  }
  // Many modal dialogs print the generated URL as plain text inside a <div>
  // instead of as an <input> or <a>. Search only for exact URL patterns.
  const pageText = document.body?.innerText || "";
  add(pageText);
  return {
    status:candidates.length === 1 ? "ONE_LINK_FOUND"
      : candidates.length === 0 ? "LINK_NOT_FOUND" : "MULTIPLE_LINKS_REVIEW_REQUIRED",
    candidates:candidates.slice(0,5),
    hints:{
      visibleInputCount:elements.filter(x=>x.tagName==="INPUT"&&x.getClientRects().length).length,
      hasPartnerLinkLabel:/파트너스\s*URL|파트너스\s*링크|생성된\s*링크/.test(pageText),
      dialogVisible:Array.from(document.querySelectorAll('[role=dialog]')).some(
        el=>el.getClientRects().length>0
      )
    }
  };
}

async function execute(action) {
  try {
    $("answer").hidden = true;
    if (action === "open") {
      // ① must never depend on an input value: reinstallation gives a fresh
      // extension with empty local storage. Validate the product only at ②.
      const raw = $("source").value.trim();
      let invalidInput = false;
      if (raw) {
        try {
          const canonical = canonicalProductURL(raw);
          // Saving a valid draft is optional; storage issues must not block
          // opening the official page in an already signed-in normal Chrome.
          try {
            await chrome.storage.local.set({pendingProductURL:canonical});
          } catch (_) {
            // No credential/session data is involved. Continue opening tab.
          }
        } catch (_) {
          invalidInput = true;
        }
      }
      await chrome.tabs.create({url:QUICK_LINK_PAGE,active:true});
      show(invalidInput
        ? "① 간편 링크 화면을 열었음. 입력 URL은 유효하지 않으니 ② 누르기 전에 일반 쿠팡 상품 URL을 확인해."
        : "① 간편 링크 화면을 열었음. 기존 로그인 확인 후 ② 상품 URL 입력을 진행해.");
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
      const url=canonicalProductURL($("source").value);
      const r=await runOnPartner(generateInPage,[url]);
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
