"use strict";
/** Runs in Node only; mocks the minimal DOM surface for the injected funcs. */
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");

const SOURCE=fs.readFileSync(path.join(__dirname,"popup.js"),"utf8");
const PRODUCT="https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724";

function node(tag,text="",value="") {
  return {
    tagName:tag, innerText:text, textContent:text, value,
    disabled:false, clickCount:0,
    getClientRects(){return [{}];},
    getAttribute(key){return this.attributes?.[key]??null;},
    click(){this.clickCount++;}
  };
}
function makePage(inputs,buttons,body="",more=[]) {
  const allElements=[...inputs,...buttons,...more];
  return {
    querySelectorAll(selector) {
      if(selector==="input, textarea")return inputs;
      if(selector==="button, input[type=button], input[type=submit]")return buttons;
      if(selector==="[role=button]")return [];
      if(selector==="input, textarea, a[href], [data-clipboard-text], [data-clipboard-target], [role=textbox]")return allElements;
      if(selector==="[role=dialog]")return [];
      return [];
    },
    body:{innerText:body},
    addEventListener(){}
  };
}
function run(document, functionName, ...args) {
  const context={document, chrome:{},getComputedStyle(){return {visibility:"visible"};}, console};
  vm.createContext(context);
  vm.runInContext(SOURCE,context);
  return JSON.parse(JSON.stringify(context[functionName](...args)));
}
const input=node("INPUT","",PRODUCT);
const green=node("BUTTON","Coupang Home");
const create=node("BUTTON","링크 생성\u200b");
assert.equal(run(makePage([input],[green,create]),"generateInPage",PRODUCT).status,"GENERATE_CLICKED");
assert.equal(create.clickCount,1, "click blue generate button once");
assert.equal(green.clickCount,0);

const nested=node("BUTTON","", "");
nested.textContent="링크\u00a0생성";
assert.equal(run(makePage([input],[green,nested]),"generateInPage",PRODUCT).status,"GENERATE_CLICKED");
assert.equal(nested.clickCount,1);

const wrongURL=node("INPUT","",PRODUCT.replace("27232840915","0000"));
const blocked=node("BUTTON","링크 생성");
assert.equal(run(makePage([wrongURL],[blocked]),"generateInPage",PRODUCT).status,"PRODUCT_URL_NOT_IN_PAGE");
assert.equal(blocked.clickCount,0);

const duplicate1=node("BUTTON","링크 생성");
const duplicate2=node("BUTTON","링크 생성");
assert.equal(run(makePage([input],[duplicate1,duplicate2]),"generateInPage",PRODUCT).status,
 "GENERATE_BUTTON_UNCONFIRMED");
assert.equal(duplicate1.clickCount+duplicate2.clickCount,0);

const plainText="파트너스 URL https://link.coupang.com/a/iIPBYPvHae";
const found=run(makePage([],[],plainText),"readGeneratedLinkInPage");
assert.equal(found.status,"ONE_LINK_FOUND");
assert.deepEqual(found.candidates,["https://link.coupang.com/a/iIPBYPvHae"]);

const duplicateResult=run(makePage([],[],
 "https://link.coupang.com/a/one https://link.coupang.com/a/two"),"readGeneratedLinkInPage");
assert.equal(duplicateResult.status,"MULTIPLE_LINKS_REVIEW_REQUIRED");
assert.equal(duplicateResult.candidates.length,2);

const nothing=run(makePage([],[],"복사"),"readGeneratedLinkInPage");
assert.equal(nothing.status,"LINK_NOT_FOUND");
console.log("7 Chrome UI selector regression checks passed");
