import test from "node:test";
import assert from "node:assert/strict";
import {classifyUpdate,coupangUrl,checkRunGate,processQueue,handleWebhook} from "./index.mjs";

const now = Date.parse("2026-11-01T03:00:00Z");
const envTemplate = {
  ENABLE_DISPATCH:"true", MAX_DAILY_DISPATCHES:"12",
  TELEGRAM_GROUP_CHAT_ID:"-100321", TRUSTED_RELAY_BOT_ID:"12345",
  TELEGRAM_WEBHOOK_SECRET:"sample_secure_local_test",
  GITHUB_TOKEN:"github_pat_just_testing_fake",
};
const stateTemplate={
  schema:"daangn-jirum-manual-fifo-v1",last_scanned_id:94743,
  pending:[], in_flight:null,
  published:{"94737":{},"94738":{},"94743":{}},
};
function db() {
  const events = new Map();
  const store={events,held:0};
  store.prepare=function(sql) {
    let args=[];
    const q={
      bind(...a){args=a;return this;},
      async run(){
        let changed=0;
        if(sql.startsWith("INSERT OR IGNORE INTO telegram_events")) {
          const key=args[0]+"/"+args[1];
          if(!events.has(key)){
            events.set(key,{chat_id:args[0],message_id:args[1],
              source_id:args[2],status:"pending",created_at_ms:args[3]});
            changed=1;
          }
        } else if(sql.startsWith("UPDATE telegram_events SET status = 'dispatch_uncertain'")) {
          const item=events.get(args[0]+"/"+args[1]);
          if(item?.status==="pending"){item.status="dispatch_uncertain";changed=1;}
        } else if(sql.startsWith("UPDATE telegram_events SET status = 'dispatched'")) {
          const item=events.get(args[0]+"/"+args[1]);
          if(item?.status==="dispatch_uncertain"){item.status="dispatched";changed=1;}
        } else if(sql.startsWith("UPDATE telegram_events SET status = 'already_scanned'")) {
          const item=events.get(args[0]+"/"+args[1]);
          if(item?.status==="pending"){item.status="already_scanned";changed=1;}
        } else if(sql.startsWith("UPDATE dispatch_lock SET held_until_ms = ?")) {
          if(store.held < args[1]){store.held=args[0];changed=1;}
        } else if(sql.startsWith("UPDATE dispatch_lock SET held_until_ms = 0")) {
          store.held=0;changed=1;
        } else throw Error("UNMATCHED_RUN_SQL:"+sql);
        return {meta:{changes:changed}};
      },
      async first(){
        if(sql.startsWith("SELECT chat_id,message_id,source_id FROM telegram_events")){
          return [...events.values()].filter(x=>x.status==="pending")
            .sort((a,b)=>a.created_at_ms-b.created_at_ms || a.message_id-b.message_id)[0] || null;
        }
        if(sql.startsWith("SELECT status FROM telegram_events")){
          const r=events.get(args[0]+"/"+args[1]);
          return r?{status:r.status}:null;
        }
        throw Error("UNMATCHED_FIRST_SQL:"+sql);
      },
    };
    return q;
  };
  return store;
}
function create(envOverrides={},stateOverrides={},runs=[]) {
  const mem=db();
  const c={...envTemplate,DB:mem,...envOverrides};
  const state={...stateTemplate,...stateOverrides};
  const calls=[];
  async function mockFetch(url,opts={}) {
    calls.push({url,method:opts.method||"GET"});
    if(url.includes("/contents/state/")) {
      return {status:200,json:async()=>({
        encoding:"base64",content:Buffer.from(JSON.stringify(state)).toString("base64"),
      })};
    }
    if(url.includes("/runs?")) return {status:200,json:async()=>({workflow_runs:runs})};
    if(url.includes("/dispatches")) return {status:204};
    throw Error("UNEXPECTED_URL:"+url);
  }
  return {env:c,mem,state,calls,mockFetch};
}
function event(text="사과 https://link.coupang.com/a/ABCxyz12",id=123) {
  return {update_id:id+10000,message:{
    message_id:id,chat:{id:-100321,type:"supergroup"},
    from:{id:12345,is_bot:true},text,
  }};
}
function req(data,secret="sample_secure_local_test") {
  return new Request("https://test.workers.dev/telegram/webhook",{
    method:"POST",headers:{"X-Telegram-Bot-Api-Secret-Token":secret},
    body:JSON.stringify(data),
  });
}
function ctx() {
  const promises=[];
  return {promises,waitUntil(p){promises.push(p);}};
}
function postCount(c){return c.calls.filter(x=>x.method==="POST").length;}
test("only Coupang domains and product shapes allowed",()=>{
  assert.ok(coupangUrl("https://link.coupang.com/a/ABCxyz12"));
  assert.ok(coupangUrl("https://www.coupang.com/vp/products/123?itemId=45"));
  assert.equal(coupangUrl("https://coupang.com.attacker.net/vp/products/123"),null);
  assert.equal(coupangUrl("https://11st.co.kr/item/123"),null);
  assert.equal(coupangUrl("https://link.coupang.com/foo"),null);
});
test("trusted relay bot in exact group is required",()=>{
  const e=event();
  assert.equal(classifyUpdate(e,envTemplate).kind,"COUPANG");
  e.message.from.id=222;
  assert.equal(classifyUpdate(e,envTemplate).kind,"IGNORE_UNTRUSTED_SENDER");
  e.message.from.id=12345;
  e.message.chat.id=-100112;
  assert.equal(classifyUpdate(e,envTemplate).kind,"IGNORE_UNTRUSTED_SENDER");
});
test("other merchant and old source are ignored",()=>{
  assert.equal(classifyUpdate(event("https://www.11st.co.kr/product/888"),envTemplate).kind,
    "IGNORE_NOT_COUPANG");
  const old=event("https://t.me/jirum/94743 https://link.coupang.com/a/ABCxyz12");
  assert.equal(classifyUpdate(old,envTemplate).kind,"IGNORE_OLD_SOURCE");
});
test("Telegram text_link entities are accepted",()=>{
  const e=event("상품 구매 링크");
  e.message.entities=[{type:"text_link",offset:0,length:2,
      url:"https://link.coupang.com/a/ABc456"}];
  assert.equal(classifyUpdate(e,envTemplate).kind,"COUPANG");
});
test("Telegram forward origin gives source post ID",()=>{
  const e=event();
  e.message.forward_origin={type:"channel",chat:{username:"jirum"},message_id:94757};
  assert.equal(classifyUpdate(e,envTemplate).sourceId,94757);
});
test("wrong webhook secret rejected before storing message",async()=>{
  const c=create();
  const response=await handleWebhook(req(event(),"wrong"),c.env,ctx(),c.mockFetch);
  assert.equal(response.status,403);
  assert.equal(c.mem.events.size,0);
  assert.equal(postCount(c),0);
});
test("one new message dispatches and persists idempotent event",async()=>{
  const c=create();
  const context=ctx();
  const a=await handleWebhook(req(event()),c.env,context,c.mockFetch);
  assert.equal(a.status,200);
  await Promise.all(context.promises);
  assert.equal(postCount(c),1);
  assert.equal(c.mem.events.get("-100321/123").status,"dispatched");
  const again=ctx();
  assert.equal((await handleWebhook(req(event()),c.env,again,c.mockFetch)).status,200);
  await Promise.all(again.promises);
  assert.equal(postCount(c),1);
});
test("new message while cooldown lock held stays queued",async()=>{
  const c=create();
  const first=ctx();await handleWebhook(req(event()),c.env,first,c.mockFetch);
  await Promise.all(first.promises);
  const second=ctx();await handleWebhook(req(event(undefined,124)),c.env,second,c.mockFetch);
  await Promise.all(second.promises);
  assert.equal(postCount(c),1);
  assert.equal(c.mem.events.get("-100321/124").status,"pending");
});
test("empty D1 and empty GitHub FIFO means ZERO github workflow actions",async()=>{
  const c=create();
  const x=await processQueue(c.env,c.mockFetch,now);
  assert.equal(x.status,"NO_NEW_COUPANG_DEAL");
  assert.equal(postCount(c),0);
});
test("existing GitHub FIFO backlog triggers only one action",async()=>{
  const c=create({}, {pending:[{id:94744,original_purchase_url:"https://link.coupang.com/a/AB12"}]});
  const x=await processQueue(c.env,c.mockFetch,now);
  assert.equal(x.status,"GITHUB_WORKFLOW_DISPATCHED");
  assert.equal(postCount(c),1);
});
test("in-flight review blocks every new Telegram trigger",async()=>{
  const c=create({}, {in_flight:{id:94744,status:"REVIEW_REQUIRED"}});
  const context=ctx();await handleWebhook(req(event()),c.env,context,c.mockFetch);
  await Promise.all(context.promises);
  assert.equal(postCount(c),0);
  assert.equal(c.mem.events.get("-100321/123").status,"pending");
});
test("already-running GitHub job blocks redundant dispatch",async()=>{
  const runs=[{head_branch:"main",event:"workflow_dispatch",status:"in_progress"}];
  const c=create({}, {pending:[{id:94744}]},runs);
  assert.equal((await processQueue(c.env,c.mockFetch,now)).status,
    "WORKFLOW_IN_PROGRESS");
  assert.equal(postCount(c),0);
});
test("daily cap of 12 Github Actions runs protects free quota",async()=>{
  const runs=Array.from({length:12},(_,i)=>({
    head_branch:"main",event:"workflow_dispatch",status:"completed",
    conclusion:"success",created_at:new Date(now-(i+1)*60000).toISOString(),
  }));
  const c=create({}, {pending:[{id:94744}]},runs);
  assert.equal((await processQueue(c.env,c.mockFetch,now)).status,
    "DAILY_RUN_BUDGET");
  assert.equal(postCount(c),0);
});
test("received event already scanned and no backlog is NOT dispatched",async()=>{
  const c=create({}, {last_scanned_id:94755});
  const e=event("https://t.me/jirum/94750 https://link.coupang.com/a/ABCxyz12");
  const context=ctx();await handleWebhook(req(e),c.env,context,c.mockFetch);
  await Promise.all(context.promises);
  assert.equal(postCount(c),0);
  assert.equal(c.mem.events.get("-100321/123").status,"already_scanned");
});
test("dispatch gate allows old success and throttles recent failed runs",()=>{
  assert.equal(checkRunGate([],12,now),"READY");
  assert.equal(checkRunGate([{
    head_branch:"main",event:"workflow_dispatch",status:"completed",
    conclusion:"failure",created_at:new Date(now-60000).toISOString(),
  }],12,now),"LAST_RUN_FAILED_COOLDOWN");
});
test("worker disabled by default",async()=>{
  const c=create({ENABLE_DISPATCH:"false"});
  assert.equal((await processQueue(c.env,c.mockFetch,now)).status,"NOT_ARMED");
  assert.equal(postCount(c),0);
});
