"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const js = fs.readFileSync(path.join(__dirname, "popup.js"), "utf8");
const target = "https://partners.coupang.com/#affiliate/ws/link-to-any-page";
const sample = "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724";

async function testOpen(value, storageFails = false) {
  const opened = [], stored = [];
  const fields = {
    source: {value},
    answer: {hidden: false},
    status: {textContent: "", style: {}}
  };
  const document = {
    getElementById(id) {return fields[id] ?? null;},
    addEventListener() {}
  };
  const chrome = {
    storage: {local: {async set(item) {
      stored.push(item);
      if (storageFails) throw Error("storage unavailable");
    }}},
    tabs: {async create(tab) {opened.push(tab);return {id: 9};}}
  };
  const ctx = {document,chrome,URL,console, navigator: {}};
  vm.createContext(ctx);
  vm.runInContext(js,ctx);
  await vm.runInContext('execute("open")',ctx);
  assert.equal(opened.length,1,"one click must open exactly one tab");
  assert.equal(opened[0].url,target);
  assert.equal(fields.answer.hidden,true);
  assert.match(fields.status.textContent,/간편 링크 화면을 열었음/);
  return {stored,status:fields.status.textContent};
}

(async()=>{
  let result = await testOpen("");
  assert.equal(result.stored.length,0,
    "freshly installed extension with empty URL must open");

  result = await testOpen("bad link");
  assert.equal(result.stored.length,0,
    "invalid input must not be stored");
  assert.match(result.status,/유효하지/);

  result = await testOpen(sample);
  assert.equal(result.stored.length,1);
  assert.equal(result.stored[0].pendingProductURL,sample);

  result = await testOpen(sample,true);
  assert.equal(result.stored.length,1,
    "a failed optional storage write must not prevent page navigation");

  console.log("4 Chrome extension open-button regressions passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
