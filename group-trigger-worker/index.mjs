/**
 * No-Oracle-change Telegram group listener -> GitHub Actions.
 *
 * A distinct receiving bot (Bot-to-Bot Communication enabled in @BotFather)
 * observes messages from the existing forwarding bot in the MUSE group.
 * Telegram POST -> Worker -> Cloudflare Queue (20s batch) -> Actions API.
 *
 * Does not reply to Telegram, does not read the original source account,
 * does not publish to Daangn itself, and never changes the Oracle server.
 */
const GH_REPO = "mw5114-tech/hotdeal-bot";
const GH_WORKFLOW = "daangn-jirum-manual-fifo.yml";
export const GH_DISPATCH_URL = `https://api.github.com/repos/${GH_REPO}/actions/workflows/${GH_WORKFLOW}/dispatches`;
const COUPANG_HOSTS = new Set([
  "link.coupang.com", "www.coupang.com", "m.coupang.com", "coupang.com",
]);
const WEBHOOK_ROUTE = "/telegram-webhook";
const URL_RE = /https?:\/\/[^\s<>"']+/gi;
const MAX_UPDATE_BYTES = 64 * 1024;

/** Match a real Coupang URL, not a misleading hostname containing 'coupang'. */
export function isCoupangUrl(value) {
  if (typeof value !== "string" || value.length > 2048) return false;
  try {
    const u = new URL(value.replace(/[),.，。\]]+$/u, ""));
    return u.protocol === "https:" && COUPANG_HOSTS.has(u.hostname) &&
      u.username === "" && u.password === "";
  } catch {
    return false;
  }
}

/** Bot API passes hidden purchase links as entities with type='text_link'. */
export function containsCoupang(message) {
  if (!message || typeof message !== "object") return false;
  const raw = [message.text, message.caption].filter((v) => typeof v === "string");
  for (const body of raw) {
    for (const match of body.matchAll(URL_RE)) {
      if (isCoupangUrl(match[0])) return true;
    }
  }
  for (const ent of [...(Array.isArray(message.entities) ? message.entities : []),
                     ...(Array.isArray(message.caption_entities) ? message.caption_entities : [])]) {
    if (ent && ent.type === "text_link" && isCoupangUrl(ent.url)) return true;
  }
  return false;
}

/** Authorize only the known group and known original sending bot. */
export function acceptedUpdate(update, env) {
  if (!update || !Number.isSafeInteger(update.update_id)) return null;
  const message = update.message ?? update.edited_message;
  if (!message || !Number.isSafeInteger(message.message_id)) return null;
  if (String(message.chat?.id) !== String(env.MUSE_GROUP_CHAT_ID)) return null;
  if (String(message.from?.id) !== String(env.SOURCE_SENDER_BOT_ID)) return null;
  if (message.from?.is_bot !== true) return null;
  if (!containsCoupang(message)) return null;
  return {
    updateId: update.update_id,
    messageId: message.message_id,
    group: String(message.chat.id),
  };
}

/** Avoid early-exit secret comparisons; no sensitive data is logged. */
function sameSecret(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !b.length) return false;
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export async function handleWebhook(req, env) {
  if (req.method !== "POST" || new URL(req.url).pathname !== WEBHOOK_ROUTE) {
    return new Response("Not found", { status: 404 });
  }
  if (!sameSecret(req.headers.get("X-Telegram-Bot-Api-Secret-Token"),
                  env.TELEGRAM_WEBHOOK_SECRET)) {
    return new Response("Forbidden", { status: 403 });
  }
  if (!env.EVENT_QUEUE || !env.MUSE_GROUP_CHAT_ID ||
      !env.SOURCE_SENDER_BOT_ID || env.SOURCE_SENDER_BOT_ID === "0") {
    return new Response("Not configured", { status: 503 });
  }
  const length = Number(req.headers.get("content-length") || 0);
  if (length > MAX_UPDATE_BYTES) return new Response("Too large", { status: 413 });
  let update;
  try {
    const body = await req.text();
    if (body.length > MAX_UPDATE_BYTES) return new Response("Too large", { status: 413 });
    update = JSON.parse(body);
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }
  const event = acceptedUpdate(update, env);
  if (!event) return new Response("Ignored", { status: 200 });
  // Telegram receives HTTP 200 only after the queue has durably accepted it.
  // On queue failure, return 503 so Telegram retries the webhook.
  try {
    await env.EVENT_QUEUE.send(event);
  } catch {
    return new Response("Queue unavailable", { status: 503 });
  }
  return new Response("Queued", { status: 200 });
}

/** Only the queue consumer calls GitHub, once for the entire 20-sec batch. */
export async function dispatchBatch(batch, env, fetchImpl = fetch) {
  if (!batch.messages?.length) return;
  if (typeof env.GITHUB_ACTIONS_TOKEN !== "string" ||
      !env.GITHUB_ACTIONS_TOKEN.length) {
    console.error("DISPATCH_CONFIG_MISSING");
    batch.retryAll({ delaySeconds: 180 });
    return;
  }
  try {
    const response = await fetchImpl(GH_DISPATCH_URL, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MUSEGroupToActions/1.0",
      },
      body: JSON.stringify({ ref: "main" }),
      signal: AbortSignal.timeout(20000),
    });
    if (response.status !== 204) {
      console.error("DISPATCH_HTTP_" + response.status);
      batch.retryAll({ delaySeconds: response.status === 429 ? 300 : 180 });
      return;
    }
    console.log("DISPATCH_ACCEPTED", { count: batch.messages.length });
    // On normal return, Cloudflare acknowledges the whole batch.
    // If a response was lost after GH accepted it, a repeated Actions run
    // is safe: the real FIFO publisher uses durable item-level claims.
  } catch {
    console.error("DISPATCH_NETWORK_ERROR");
    batch.retryAll({ delaySeconds: 180 });
  }
}

export default {
  fetch(request, env, ctx) {
    return handleWebhook(request, env);
  },
  async queue(batch, env, ctx) {
    await dispatchBatch(batch, env);
  },
};
