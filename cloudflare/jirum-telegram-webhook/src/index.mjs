/**
 * Event-driven Telegram -> Github Actions dispatcher.
 * Cloudflare Workers Free + D1: only POST a GitHub workflow_dispatch when
 * a trusted Telegram relay bot posts a Coupang deal or FIFO backlog exists.
 * Existing main-branch FIFO is the authoritative posting and dedup state.
 */
const REPO = "mw5114-tech/hotdeal-bot";
const WORKFLOW = "daangn-jirum-manual-fifo.yml";
const GH = "https://api.github.com/repos/" + REPO;
const COOLDOWN_MS = 180000;
const START_AFTER = 94743;

export function coupangUrl(raw) {
  if (typeof raw !== "string" || raw.length > 2500) return null;
  try {
    const u = new URL(raw.replace(/&amp;/g, "&").replace(/[),，。]+$/, ""));
    if (u.protocol !== "https:" || u.username || u.password) return null;
    if (u.hostname === "link.coupang.com" &&
        /^\/a\/[A-Za-z0-9]+\/?$/.test(u.pathname)) return u.href;
    if (["www.coupang.com", "coupang.com"].includes(u.hostname) &&
        /^\/vp\/products\/\d+\/?$/.test(u.pathname)) return u.href;
    return null;
  } catch {
    return null;
  }
}
function extractUrls(msg) {
  const text = msg.text || msg.caption || "";
  const entities = msg.entities || msg.caption_entities || [];
  const values = [];
  for (const e of entities) {
    if (e.type === "text_link" && typeof e.url === "string") values.push(e.url);
    if (e.type === "url" && Number.isInteger(e.offset) && Number.isInteger(e.length)) {
      values.push(text.slice(e.offset, e.offset + e.length));
    }
  }
  const found = text.match(/https:\/\/[^\s<>"']+/g);
  if (found) values.push(...found);
  return values;
}
function sourcePostId(msg) {
  const origin = msg.forward_origin;
  if (origin?.type === "channel" &&
      origin.chat?.username?.toLowerCase() === "jirum" &&
      Number.isSafeInteger(origin.message_id)) return origin.message_id;
  const text = (msg.text || msg.caption || "");
  const found = text.match(/(?:https:\/\/)?t\.me\/(?:s\/)?jirum\/(\d+)/i);
  return found ? Number(found[1]) : null;
}
export function classifyUpdate(update, env) {
  const msg = update?.message || update?.channel_post;
  if (!msg || !Number.isSafeInteger(msg.message_id)) return {kind:"IGNORE_NOT_MESSAGE"};
  if (String(msg.chat?.id) !== String(env.TELEGRAM_GROUP_CHAT_ID) ||
      String(msg.from?.id) !== String(env.TRUSTED_RELAY_BOT_ID) ||
      msg.from?.is_bot !== true) return {kind:"IGNORE_UNTRUSTED_SENDER"};
  const sid = sourcePostId(msg);
  if (sid !== null && (!Number.isSafeInteger(sid) || sid <= START_AFTER)) {
    return {kind:"IGNORE_OLD_SOURCE"};
  }
  const deal = extractUrls(msg).map(coupangUrl).find(Boolean);
  if (!deal) return {kind:"IGNORE_NOT_COUPANG"};
  return {kind:"COUPANG", chatId:String(msg.chat.id),
          messageId:msg.message_id, sourceId:sid};
}
function checkEnv(env) {
  if (!env?.DB || typeof env.DB.prepare !== "function") throw Error("D1_BINDING_MISSING");
  if (!env.TELEGRAM_GROUP_CHAT_ID || !env.TRUSTED_RELAY_BOT_ID)
    throw Error("TRUSTED_TELEGRAM_IDS_MISSING");
  if (env.ENABLE_DISPATCH !== "true") return false;
  if (!env.GITHUB_TOKEN || !env.TELEGRAM_WEBHOOK_SECRET)
    throw Error("WEBHOOK_OR_GITHUB_SECRET_MISSING");
  const cap = Number(env.MAX_DAILY_DISPATCHES || "12");
  if (!Number.isInteger(cap) || cap < 1 || cap > 40) throw Error("BAD_DAILY_CAP");
  return true;
}
function ghHeaders(env) {
  return {
    Authorization: "Bearer " + env.GITHUB_TOKEN,
    Accept: "application/vnd.github+json",
    "User-Agent": "DaangnTelegramWebhook/1.0",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}
async function ghGet(env, url, fetcher) {
  const res = await fetcher(url, {headers:ghHeaders(env), redirect:"manual"});
  if (res.status !== 200) throw Error("GITHUB_READ_FAILED_" + res.status);
  return res.json();
}
export function checkRunGate(runs, cap, nowMs) {
  if (!Array.isArray(runs)) throw Error("RUNS_NOT_ARRAY");
  const own = runs.filter(r=>r.head_branch === "main" &&
    ["workflow_dispatch","push"].includes(r.event));
  if (own.some(r=>r.status !== "completed")) return "WORKFLOW_IN_PROGRESS";
  const withinDay = own.filter(r=>{
    const delta = nowMs - Date.parse(r.created_at);
    return Number.isFinite(delta) && delta >= 0 && delta < 86400000;
  });
  if (withinDay.length >= cap) return "DAILY_RUN_BUDGET";
  const latest = own.slice().sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at))[0];
  if (latest && latest.conclusion !== "success" &&
      nowMs-Date.parse(latest.created_at) < 1800000) return "LAST_RUN_FAILED_COOLDOWN";
  return "READY";
}
async function readGithubState(env, fetcher) {
  const data = await ghGet(env, GH + "/contents/state/daangn_jirum_manual_fifo.json?ref=main",fetcher);
  if (data.encoding !== "base64" || typeof data.content !== "string")
    throw Error("GITHUB_STATE_UNAVAILABLE");
  const binary = atob(data.content.replace(/\s/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  const state = JSON.parse(new TextDecoder().decode(bytes));
  if (state.schema !== "daangn-jirum-manual-fifo-v1" ||
      !Number.isSafeInteger(state.last_scanned_id) ||
      state.last_scanned_id < START_AFTER ||
      !Array.isArray(state.pending) || state.pending.length > 300)
    throw Error("GITHUB_QUEUE_STATE_INVALID");
  return state;
}
async function claimLock(db, now) {
  const r=await db.prepare(
    "UPDATE dispatch_lock SET held_until_ms = ? WHERE id = 1 AND held_until_ms < ?"
  ).bind(now + COOLDOWN_MS,now).run();
  return r.meta?.changes === 1;
}
async function releaseLock(db) {
  await db.prepare("UPDATE dispatch_lock SET held_until_ms = 0 WHERE id = 1").run();
}
export async function processQueue(env, fetcher = fetch, nowMs = Date.now()) {
  if (!checkEnv(env)) return {status:"NOT_ARMED"};
  if (!await claimLock(env.DB,nowMs)) return {status:"LOCKED_OR_COOLDOWN"};
  let dispatched = false;
  let event = null;
  try {
    // A POST with a lost/failed response may have reached GitHub.
    // Stop all follow-up automated dispatches until manually reconciled.
    const uncertain = await env.DB.prepare(
      "SELECT chat_id,message_id FROM telegram_events WHERE status = 'dispatch_uncertain' ORDER BY created_at_ms LIMIT 1"
    ).first();
    if (uncertain) return {status:"DISPATCH_UNCERTAIN_REVIEW_REQUIRED"};
    event=await env.DB.prepare(
      "SELECT chat_id,message_id,source_id FROM telegram_events WHERE status = 'pending' ORDER BY created_at_ms, message_id LIMIT 1"
    ).first();
    // One inexpensive GitHub state read per minute, but no idle Actions runs.
    const state = await readGithubState(env, fetcher);
    if (state.in_flight !== null) return {status:"POST_CLAIM_OR_REVIEW_REQUIRED"};
    const backlog = state.pending.length > 0;
    if (event && event.source_id != null &&
        event.source_id <= state.last_scanned_id && !backlog) {
      await env.DB.prepare(
        "UPDATE telegram_events SET status = 'already_scanned' WHERE chat_id = ? AND message_id = ? AND status = 'pending'"
      ).bind(event.chat_id,event.message_id).run();
      return {status:"SOURCE_ALREADY_SCANNED"};
    }
    if (!event && !backlog) return {status:"NO_NEW_COUPANG_DEAL"};
    const runs = await ghGet(env, GH + "/actions/workflows/" +
      WORKFLOW + "/runs?branch=main&per_page=100",fetcher);
    const gate = checkRunGate(runs.workflow_runs,Number(env.MAX_DAILY_DISPATCHES || "12"),nowMs);
    if (gate !== "READY") return {status:gate};
    // Mark uncertain BEFORE making any outgoing dispatch POST. If the
    // API response is lost, an automatic duplicate POST is not allowed.
    if (event) {
      const updated = await env.DB.prepare(
        "UPDATE telegram_events SET status = 'dispatch_uncertain' WHERE chat_id = ? AND message_id = ? AND status = 'pending'"
      ).bind(event.chat_id,event.message_id).run();
      if (updated.meta?.changes !== 1) return {status:"EVENT_ALREADY_CLAIMED"};
    }
    const response = await fetcher(GH+"/actions/workflows/"+WORKFLOW+"/dispatches",{
      method:"POST", headers:{...ghHeaders(env),"Content-Type":"application/json"},
      body:JSON.stringify({ref:"main"}),redirect:"manual",
    });
    if (response.status !== 204) throw Error("DISPATCH_HTTP_"+response.status);
    dispatched = true;
    if (event) {
      await env.DB.prepare(
        "UPDATE telegram_events SET status = 'dispatched' WHERE chat_id = ? AND message_id = ? AND status = 'dispatch_uncertain'"
      ).bind(event.chat_id,event.message_id).run();
    }
    return {status:"GITHUB_WORKFLOW_DISPATCHED",reason:backlog?"FIFO_BACKLOG":"NEW_COUPANG"};
  } finally {
    // Keep 3-min guard after POST (success or unknown) so GitHub's newly
    // created run becomes visible to read-before-write checks.
    if (!dispatched && (!event || !await eventIsUncertain(env.DB,event))) {
      await releaseLock(env.DB);
    }
  }
}
async function eventIsUncertain(db,event) {
  if (!event) return false;
  const row=await db.prepare(
    "SELECT status FROM telegram_events WHERE chat_id = ? AND message_id = ?"
  ).bind(event.chat_id,event.message_id).first();
  return row?.status === "dispatch_uncertain";
}
export async function handleWebhook(request, env, context, fetcher=fetch) {
  if (request.method !== "POST" || new URL(request.url).pathname !== "/telegram/webhook") {
    return new Response("Not Found",{status:404});
  }
  // Reject unknown callers before parsing body or touching D1.
  if (!env.TELEGRAM_WEBHOOK_SECRET ||
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET) {
    return new Response("Forbidden",{status:403});
  }
  if (!checkEnv(env)) return new Response("Not armed",{status:503});
  const raw=await request.text();
  if (raw.length > 50000) return new Response("Too large",{status:413});
  let update;
  try {update=JSON.parse(raw);} catch {return new Response("Bad json",{status:400});}
  const match=classifyUpdate(update,env);
  if (match.kind !== "COUPANG") return new Response("Ignored",{status:200});
  const recorded=await env.DB.prepare(
    "INSERT OR IGNORE INTO telegram_events(chat_id,message_id,source_id,status,created_at_ms) VALUES(?,?,?,'pending',?)"
  ).bind(match.chatId,match.messageId,match.sourceId,Date.now()).run();
  if (recorded.meta?.changes === 1) {
    context.waitUntil(processQueue(env,fetcher).then(
      x=>console.log("TELEGRAM_DISPATCH "+x.status),
      e=>console.error("TELEGRAM_DISPATCH_ERROR "+(e?.message || "UNKNOWN"))));
  }
  return new Response("OK",{status:200});
}
export default {
  fetch(request,env,context) {return handleWebhook(request,env,context);},
  scheduled(_controller,env,context) {
    context.waitUntil(processQueue(env).then(
      x=>console.log("TELEGRAM_RECOVERY "+x.status),
      e=>console.error("TELEGRAM_RECOVERY_ERROR "+(e?.message || "UNKNOWN"))));
  },
};
