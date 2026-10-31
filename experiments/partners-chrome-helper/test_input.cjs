"use strict";
/** Offline DOM contract reproducing real user's screenshots: input labelled "url". */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "popup.js"), "utf8");
const PRODUCT = "https://www.coupang.com/vp/products/9217210892?itemId=27232840915&vendorItemId=94199974724";

class FakeInput {
  constructor(label) {
    this.tagName="INPUT";
    this.type="text";
    this.disabled=false;
    this.attrs={placeholder:label};
    this._value="";
    this.events=[];
  }
  get value() {return this._value;}
  set value(v) {this._value=v;}
  getAttribute(name) {return this.attrs[name] ?? null;}
  getClientRects() {return [{}];}
  closest() {return null;}
  dispatchEvent(e) {this.events.push(e.type); return true;}
  focus() {this.focused=true;}
}
class FakeTextarea extends FakeInput {}
class FakeEvent {
  constructor(type,options) {this.type=type;this.bubbles=!!options?.bubbles;}
}
class FakeButton {
  constructor(text) {this.tagName="BUTTON";this.innerText=text;this.textContent=text;this.disabled=false;this.clicks=0;}
  getClientRects() {return [{}];}
  getAttribute() {return null;}
  click() {this.clicks++;}
}
function view(inputs,buttons=[],label="쿠팡 URL") {
  const header={innerText:label,getClientRects(){return [{}];}};
  return {
    querySelectorAll(selector) {
      if(selector==="input, textarea") return inputs;
      if(selector==="label, h1, h2, h3, h4") return label ? [header] : [];
      if(selector==="button, input[type=button], input[type=submit]") return buttons;
      if(selector==="[role=button]") return [];
      return [];
    },
    addEventListener(){}
  };
}
function run(doc,func,...args) {
  const ctx={document:doc,HTMLInputElement:FakeInput,HTMLTextAreaElement:FakeTextarea,
             Event:FakeEvent,getComputedStyle:()=>({visibility:"visible"}),chrome:{}, console};
  vm.createContext(ctx);
  vm.runInContext(source,ctx);
  return JSON.parse(JSON.stringify(ctx[func](...args)));
}

const field=new FakeInput("url"); // Real screen's diagnostic candidates:["url"]
const button=new FakeButton("링크 생성");
const page=view([field],[button]);
const filled=run(page,"inputInPage",PRODUCT);
assert.equal(filled.status,"FILLED");
assert.equal(filled.exactValueConfirmed,true);
assert.equal(field.value,PRODUCT);
assert.deepEqual(field.events,["input","change"]);
assert.equal(field.focused,true);
const generated=run(page,"generateInPage",PRODUCT);
assert.equal(generated.status,"GENERATE_CLICKED");
assert.equal(button.clicks,1);
console.log("✓ Real 'url' field accepted then exact link clicked once");

const renamed=new FakeInput("mystery_field");
const r1=run(view([renamed],[],"쿠팡 URL"),"inputInPage",PRODUCT);
assert.equal(r1.status,"FILLED","one field under labelled quick-link form is acceptable");
assert.equal(renamed.value,PRODUCT);

const unrelated=new FakeInput("mystery_field");
const r2=run(view([unrelated],[],""),"inputInPage",PRODUCT);
assert.equal(r2.status,"INPUT_UNCONFIRMED");
assert.equal(unrelated.value,"","no random input modifications on non-quicklink pages");

const a=new FakeInput("url"), b=new FakeInput("URL");
const r3=run(view([a,b],[],"쿠팡 URL"),"inputInPage",PRODUCT);
assert.equal(r3.status,"INPUT_UNCONFIRMED","ambiguous inputs cannot be guessed");
assert.equal(a.value,"");
assert.equal(b.value,"");

const wrong=new FakeInput("url");
const stop=new FakeButton("링크 생성");
const fail=run(view([wrong],[stop]),"generateInPage",PRODUCT);
assert.equal(fail.status,"PRODUCT_URL_NOT_IN_PAGE");
assert.equal(stop.clicks,0,"never create a link when input does not match");
console.log("5 Chrome live-screenshot input/create regression scenarios passed");
