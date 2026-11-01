import test from "node:test";
import {DatabaseSync} from "node:sqlite";
import {readFileSync,unlinkSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
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
function db(path=":memory:",initialize=true) {
  const sql = new DatabaseSync(path);
  for (const name of initialize ? ["0001_init.sql", "0002_dispatch_attempts.sql"] : []) {
    sql.exec(readFileSync(new URL("../migrations/"+name,import.meta.url),"utf8"));
  }
  return {sql,
    events:{
      get size(){return sql.prepare("SELECT COUNT(*) n FROM telegram_events").get().n;},
      get(key){const [chat,mid]=key.split("/");return sql.prepare("SELECT * FROM telegram_events WHERE chat_id=? AND message_id=?").get(chat,Number(mid));},
      set(key,e){sql.prepare("INSERT INTO telegram_events(chat_id,message_id,source_id,status,created_at_ms) VALUES(?,?,?,?,?)").run(e.chat_id,e.message_id,e.source_id,e.status,e.created_at_ms);}
    },
    prepare(query){let args=[];return {
      bind(...values){args=values;return this;},
      async run(){return {meta:{changes:sql.prepare(query).run(...args).changes}};},
      async first(){return sql.prepare(query).get(...args) || null;}
    };}
  };
}
function create(envOverrides={},stateOverrides={},runs=[]) {
  const mem=db();
  const c={...envTemplate,DB:mem,...envOverrides};
  const state={...stateTemplate,...stateOverrides};
  const calls=[];
  async function mockFetch(url,opts={}) {
    calls.push({url,method:opts.method||"GET",body:opts.body});
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
test("source watermark does not discard group product content",async()=>{
  const c=create({}, {last_scanned_id:94755});
  const e=event("https://t.me/jirum/94750 https://link.coupang.com/a/ABCxyz12");
  const context=ctx();await handleWebhook(req(e),c.env,context,c.mockFetch);
  await Promise.all(context.promises);
  assert.equal(postCount(c),1);
  assert.equal(c.mem.events.get("-100321/123").status,"dispatched");
});
test("dispatch gate allows old success and throttles recent failed runs",()=>{
  assert.equal(checkRunGate([],12,now),"READY");
  assert.equal(checkRunGate([{
    head_branch:"main",event:"workflow_dispatch",status:"completed",
    conclusion:"failure",created_at:new Date(now-60000).toISOString(),
  }],12,now),"LAST_RUN_FAILED_REVIEW_REQUIRED");
});
test("unconfirmed GitHub dispatch blocks all future automatic actions",async()=>{
  const c=create({}, {pending:[{id:94744}]});
  c.mem.events.set("-100321/123", {
    chat_id:"-100321",message_id:123,source_id:94744,
    status:"dispatch_uncertain",created_at_ms:now-60000
  });
  assert.equal((await processQueue(c.env,c.mockFetch,now)).status,
    "DISPATCH_UNCERTAIN_REVIEW_REQUIRED");
  assert.equal(postCount(c),0);
});
test("worker disabled by default",async()=>{
  const c=create({ENABLE_DISPATCH:"false"});
  assert.equal((await processQueue(c.env,c.mockFetch,now)).status,"NOT_ARMED");
  assert.equal(postCount(c),0);
});

for (const withEvent of [false,true]) test(`lost dispatch response persists global stop (event=${withEvent})`,async()=>{
  const c=create({}, {pending:[{id:94744}]});
  let posts=0;
  const failed=async(url,opts={})=>{
    if(opts.method==="POST"){posts++;throw Error("response lost");}
    return c.mockFetch(url,opts);
  };
  if(withEvent){
    const context=ctx();
    await handleWebhook(req(event()),c.env,context,failed);
    await Promise.all(context.promises);
  } else await assert.rejects(processQueue(c.env,failed,now),/response lost/);
  // Fresh invocation, expired lease, same persistent database.
  const later=Date.now()+86400000;
  assert.equal((await processQueue(c.env,failed,later)).status,"DISPATCH_UNCERTAIN_REVIEW_REQUIRED");
  assert.equal(posts,1);
  assert.equal(c.mem.sql.prepare("SELECT status FROM dispatch_attempts").get().status,"uncertain");
});

test("accepted request not visible in runs never causes another POST",async()=>{
  const c=create({}, {pending:[{id:94744}]});
  await processQueue(c.env,c.mockFetch,now);
  assert.equal((await processQueue(c.env,c.mockFetch,now+600000)).status,"DISPATCH_VISIBILITY_REVIEW_REQUIRED");
  assert.equal(postCount(c),1);
});

test("group-only content is transmitted intact as JSON input",async()=>{
  const c=create(); const context=ctx();
  const e=event('상품 9,900원 https://link.coupang.com/a/AB12\n$(never execute)');
  await handleWebhook(req(e),c.env,context,c.mockFetch); await Promise.all(context.promises);
  const input=JSON.parse(c.calls.find(x=>x.method==="POST").body).inputs;
  assert.equal(JSON.parse(input.telegram_payload).text,e.message.text);
  assert.equal(JSON.parse(input.telegram_payload).source_id,null);
  assert.match(input.request_id,/^[a-f0-9-]{36}$/);
});

test("capture-only receives and deduplicates without GitHub calls",async()=>{
  const c=create({ENABLE_DISPATCH:"false"});const context=ctx();
  await handleWebhook(req(event()),c.env,context,c.mockFetch);
  await handleWebhook(req(event()),c.env,context,c.mockFetch);
  await Promise.all(context.promises);
  assert.equal(c.mem.events.size,1);assert.equal(c.calls.length,0);
  assert.equal(c.mem.events.get("-100321/123").status,"pending");
});

test("non-Coupang webhook never stores or dispatches",async()=>{
  const c=create();const context=ctx();
  await handleWebhook(req(event('https://11st.co.kr/product/123')),c.env,context,c.mockFetch);
  await Promise.all(context.promises);
  assert.equal(c.mem.events.size,0);assert.equal(c.calls.length,0);
});

test("SQLite unique constraint prevents overlapping attempts even after lease expiry",()=>{
  const c=create();
  c.mem.sql.prepare("INSERT INTO dispatch_attempts VALUES(?,?,?)").run("one","accepted",now);
  assert.throws(()=>c.mem.sql.prepare("INSERT INTO dispatch_attempts VALUES(?,?,?)").run("two","uncertain",now+600000),/UNIQUE/);
});

test("failed run remains blocked after thirty minutes",()=>{
  assert.equal(checkRunGate([{head_branch:"main",event:"workflow_dispatch",status:"completed",conclusion:"failure",created_at:new Date(now-86400000).toISOString()}],12,now),"LAST_RUN_FAILED_REVIEW_REQUIRED");
});

test("closed and reopened database retains uncertain backlog dispatch",async()=>{
  const path=join(tmpdir(),`hotdeal-test-${crypto.randomUUID()}.sqlite`);
  const c=create({}, {pending:[{id:94744}]});
  let disk=db(path);c.env.DB=disk;let posts=0;
  const failed=async(url,opts={})=>{if(opts.method==='POST'){posts++;throw Error('lost');}return c.mockFetch(url,opts);};
  try {
    await assert.rejects(processQueue(c.env,failed,now),/lost/);
    disk.sql.close();disk=db(path,false);c.env.DB=disk;
    assert.equal((await processQueue(c.env,failed,now+86400000)).status,'DISPATCH_UNCERTAIN_REVIEW_REQUIRED');
    assert.equal(posts,1);
  } finally {disk.sql.close();unlinkSync(path);}
});

test("accepted successful run permits next queued event only after completion",async()=>{
  const runs=[];const c=create({}, {pending:[{id:94744}]},runs);
  await processQueue(c.env,c.mockFetch,now);
  const input=JSON.parse(c.calls.find(x=>x.method==='POST').body).inputs;
  runs.push({display_title:'telegram-'+input.request_id,head_branch:'main',event:'workflow_dispatch',status:'completed',conclusion:'success',created_at:new Date(now).toISOString()});
  c.state.pending=[];
  assert.equal((await processQueue(c.env,c.mockFetch,now+600000)).status,'NO_NEW_COUPANG_DEAL');
  assert.equal(postCount(c),1);
});

test("daily cap leaves event pending and resumes after rolling window",async()=>{
  const runs=Array.from({length:12},()=>({head_branch:'main',event:'workflow_dispatch',status:'completed',conclusion:'success',created_at:new Date(Date.now()-60000).toISOString()}));
  const c=create({}, {},runs);const context=ctx();
  await handleWebhook(req(event()),c.env,context,c.mockFetch);await Promise.all(context.promises);
  assert.equal(postCount(c),0);assert.equal(c.mem.events.get('-100321/123').status,'pending');
  assert.equal((await processQueue(c.env,c.mockFetch,Date.now()+86400000)).status,'GITHUB_WORKFLOW_DISPATCHED');
  assert.equal(postCount(c),1);
});
