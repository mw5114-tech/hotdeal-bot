/**
 * Cloudflare Workers Free: poll @jirum once per minute but dispatch GitHub
 * Actions ONLY for a new Coupang item or persisted queue backlog.
 *
 * Stateless by design: main-branch GitHub FIFO JSON is authoritative.
 * No KV, Durable Objects, public endpoint or new paid service is required.
 * Unclear data or GitHub outages fail closed; no workflow is dispatched.
 */
const DEFAULT_REPO = "mw5114-tech/hotdeal-bot";
const DEFAULT_WORKFLOW = "daangn-jirum-manual-fifo.yml";
const START_AFTER = 94743;
const MAX_PAGES = 40;
const RUN_COOLDOWN_MINUTES = 30;
const GH_API = "https://api.github.com";

function requireConfig(env) {
  const token = env.GITHUB_TOKEN;
  const repo = env.GITHUB_REPOSITORY || DEFAULT_REPO;
  const workflow = env.TARGET_WORKFLOW || DEFAULT_WORKFLOW;
  const branch = env.TARGET_BRANCH || "main";
  const enabled = env.ENABLE_DISPATCH === "true";
  const dailyCap = Number(env.MAX_DAILY_DISPATCHES || "12");
  if (repo !== DEFAULT_REPO || branch !== "main" || workflow !== DEFAULT_WORKFLOW) {
    throw new Error("UNEXPECTED_GITHUB_DESTINATION");
  }
  if (!Number.isInteger(dailyCap) || dailyCap < 1 || dailyCap > 60) {
    throw new Error("INVALID_DAILY_DISPATCH_LIMIT");
  }
  if (enabled && (typeof token !== "string" || token.length < 10)) {
    throw new Error("GITHUB_TOKEN_NOT_SET");
  }
  return { token, repo, workflow, branch, enabled, dailyCap };
}

async function fetchWithDeadline(fetcher, url, init = {}, ms = 12000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ms);
  try {
    return await fetcher(url, { ...init, signal: controller.signal, redirect: "manual" });
  } finally {
    clearTimeout(timeout);
  }
}

function decodeContents(item) {
  if (!item || item.type !== "file" || item.encoding !== "base64" ||
      typeof item.content !== "string" || !item.sha) {
    throw new Error("GITHUB_STATE_NOT_READABLE");
  }
  const bytes = Uint8Array.from(atob(item.content.replace(/\s+/g, "")), c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function checkState(state) {
  if (state?.schema !== "daangn-jirum-manual-fifo-v1" ||
      !Number.isSafeInteger(state.last_scanned_id) ||
      state.last_scanned_id < START_AFTER ||
      !Array.isArray(state.pending) ||
      !state.published || typeof state.published !== "object") {
    throw new Error("INVALID_GITHUB_FIFO_STATE");
  }
  if (!["94737", "94738", "94743"].every(k => Object.hasOwn(state.published, k))) {
    throw new Error("HISTORICAL_GUARDS_NOT_PRESENT");
  }
  if (state.pending.length > 300 ||
      state.pending.some(p => !Number.isSafeInteger(p.id) || p.id <= START_AFTER)) {
    throw new Error("INVALID_FIFO_PENDING_ITEMS");
  }
  return state;
}

function githubHeaders(token) {
  return {
    Authorization: "Bearer " + token,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "JirumCoupangMinutePoller/1.0",
  };
}

async function getGithubJson(fetcher, url, token) {
  const response = await fetchWithDeadline(fetcher, url, { headers: githubHeaders(token) });
  if (response.status !== 200) {
    throw new Error("GITHUB_GET_HTTP_" + response.status);
  }
  return response.json();
}

async function fetchGithubState(fetcher, c) {
  const u = GH_API + "/repos/" + c.repo +
    "/contents/state/daangn_jirum_manual_fifo.json?ref=main";
  return checkState(decodeContents(await getGithubJson(fetcher, u, c.token)));
}

async function fetchWorkflowRuns(fetcher, c) {
  const url = GH_API + "/repos/" + c.repo + "/actions/workflows/" +
    c.workflow + "/runs?branch=main&per_page=100";
  const result = await getGithubJson(fetcher, url, c.token);
  if (!Array.isArray(result.workflow_runs)) {
    throw new Error("WORKFLOW_RUNS_UNAVAILABLE");
  }
  return result.workflow_runs;
}

// Handles ampersands and numeric HTML entities from Telegram hrefs.
function unescapeHtml(s) {
  return s
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:x([0-9a-f]+)|([0-9]+));/gi,
      (_, hex, dec) => String.fromCodePoint(parseInt(hex || dec, hex ? 16 : 10)));
}

function safePurchaseUrl(raw) {
  if (!raw) return null;
  try {
    const url = new URL(unescapeHtml(raw.trim().replace(/[.,，。)]+$/, "")));
    if (url.protocol !== "https:" || url.username || url.password) return null;
    if (url.hostname === "t.me" || url.hostname.endsWith(".t.me") ||
        url.hostname === "telegram.org") return null;
    if (url.hostname === "link.coupang.com" &&
        /^\/a\/[A-Za-z0-9]+\/?$/.test(url.pathname)) {
      return { kind: "coupang", url: url.href };
    }
    if (["www.coupang.com", "coupang.com"].includes(url.hostname) &&
        /^\/vp\/products\/\d+\/?$/.test(url.pathname)) {
      return { kind: "coupang", url: url.href };
    }
    return { kind: "external", url: url.href };
  } catch {
    return null;
  }
}

function extractPurchaseUrl(html) {
  const candidates = [];
  for (const match of html.matchAll(/<a\b[^>]*?\bhref\s*=\s*(?:"([^"]+)"|'([^']+)')[^>]*>/gi)) {
    const url = safePurchaseUrl(match[1] || match[2]);
    if (url) candidates.push(url);
  }
  const text = unescapeHtml(html.replace(/<[^>]+>/g, " "));
  for (const match of text.matchAll(/https:\/\/[^\s<>"]+/g)) {
    const url = safePurchaseUrl(match[0]);
    if (url) candidates.push(url);
  }
  // Source parser prioritizes the earliest actual linked purchase URL.
  return candidates.length ? candidates[0] : null;
}

export function parseTelegramMessages(html) {
  if (typeof html !== "string" || html.length > 2_000_000) {
    throw new Error("BAD_TELEGRAM_HTML");
  }
  const markers = [...html.matchAll(/data-post\s*=\s*["']jirum\/(\d+)["']/g)];
  if (!markers.length) {
    throw new Error("TELEGRAM_POST_MARKERS_MISSING");
  }
  const out = new Map();
  for (let i = 0; i < markers.length; i++) {
    const id = Number(markers[i][1]);
    if (!Number.isSafeInteger(id) || id <= 0) continue;
    const chunk = html.slice(markers[i].index, markers[i + 1]?.index ?? html.length);
    const body = chunk.match(/<div\b[^>]*class\s*=\s*["'][^"']*tgme_widget_message_text[^"']*["'][^>]*>([\s\S]*?)<\/div>/i);
    if (!body) {
      // Do not accidentally treat links in other Telegram page widgets as products.
      out.set(id, { id, urlKind: "none", textAvailable: false });
      continue;
    }
    const purchase = extractPurchaseUrl(body[1]);
    out.set(id, { id, urlKind: purchase?.kind ?? "none", textAvailable: true });
  }
  if (!out.size) throw new Error("TELEGRAM_POST_IDS_INVALID");
  return [...out.values()].sort((a, b) => a.id - b.id);
}

async function fetchTelegramPage(fetcher, before) {
  const url = before === null ?
    "https://t.me/s/jirum" : "https://t.me/s/jirum?before=" + before;
  const response = await fetchWithDeadline(fetcher, url, {
    headers: {
      Accept: "text/html",
      "User-Agent": "Mozilla/5.0 (compatible; DealWatcher/1.0)",
    },
  }, 15000);
  if (response.status !== 200 || new URL(response.url).hostname !== "t.me") {
    throw new Error("TELEGRAM_HTTP_OR_REDIRECT_" + response.status);
  }
  const html = await response.text();
  return parseTelegramMessages(html);
}

export async function hasUnseenCoupang(fetcher, watermark, limit = MAX_PAGES) {
  if (!Number.isSafeInteger(watermark) || watermark < START_AFTER) {
    throw new Error("INVALID_SCAN_WATERMARK");
  }
  let before = null;
  let lastOldest = Number.POSITIVE_INFINITY;
  let seenFresh = 0;
  let foundCoupang = false;
  let pageCount = 0;
  for (let page = 0; page < limit; page++) {
    const entries = await fetchTelegramPage(fetcher, before);
    pageCount++;
    const oldest = entries[0].id;
    if (oldest >= lastOldest) {
      throw new Error("TELEGRAM_PAGINATION_NOT_ADVANCING");
    }
    for (const e of entries) {
      if (e.id > watermark) {
        seenFresh++;
        if (!e.textAvailable) {
          // An unseen message could contain a Coupang link. Never silently
          // treat missing source text as confirmed "no new Coupang".
          throw new Error("UNSEEN_TELEGRAM_TEXT_MISSING");
        }
        if (e.urlKind === "coupang") foundCoupang = true;
      }
    }
    if (foundCoupang) {
      return { found: true, seenFresh, pageCount };
    }
    if (oldest <= watermark) {
      return { found: false, seenFresh, pageCount };
    }
    before = oldest;
    lastOldest = oldest;
  }
  throw new Error("TELEGRAM_HISTORY_TOO_DEEP_NO_DISPATCH");
}

function runAgeMs(run, now) {
  const time = Date.parse(run.created_at || "");
  return Number.isFinite(time) ? now - time : -1;
}

export function evaluateRunGate(runs, cap, now = Date.now()) {
  if (!Array.isArray(runs)) throw new Error("BAD_WORKFLOW_RUNS");
  const own = runs.filter(r => r?.head_branch === "main" &&
    ["workflow_dispatch", "push"].includes(r.event));
  if (own.some(r => r.status !== "completed")) {
    return "WORKFLOW_ALREADY_RUNNING";
  }
  const recent24h = own.filter(r => runAgeMs(r, now) >= 0 &&
    runAgeMs(r, now) < 24 * 3600 * 1000);
  if (recent24h.length >= cap) return "DAILY_DISPATCH_BUDGET_REACHED";
  const latest = own.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  if (latest && latest.conclusion !== "success" &&
      runAgeMs(latest, now) >= 0 &&
      runAgeMs(latest, now) < RUN_COOLDOWN_MINUTES * 60_000) {
    return "RECENT_FAILED_RUN_COOLDOWN";
  }
  return "CAN_DISPATCH";
}

async function dispatchGithub(fetcher, c) {
  const url = GH_API + "/repos/" + c.repo +
    "/actions/workflows/" + c.workflow + "/dispatches";
  const res = await fetchWithDeadline(fetcher, url, {
    method: "POST",
    headers: { ...githubHeaders(c.token), "Content-Type": "application/json" },
    body: JSON.stringify({ ref: c.branch }),
  }, 15000);
  if (res.status !== 204) {
    // Never retry a dispatch automatically on timeout: GitHub might have accepted it.
    throw new Error("GITHUB_DISPATCH_HTTP_" + res.status);
  }
  return true;
}

export async function tick(env, fetcher = fetch, now = Date.now()) {
  const c = requireConfig(env);
  if (!c.enabled) return { status: "NOT_ARMED" };
  const state = await fetchGithubState(fetcher, c);
  if (state.in_flight !== null) {
    return { status: "REVIEW_OR_CLAIM_IN_PROGRESS" };
  }
  let reason = "NEW_COUPANG_FOUND";
  if (state.pending.length === 0) {
    const res = await hasUnseenCoupang(fetcher, state.last_scanned_id);
    if (!res.found) {
      return { status: "NO_NEW_COUPANG_DEAL", scannedPages: res.pageCount };
    }
  } else {
    reason = "EXISTING_FIFO_BACKLOG";
  }
  // Check active and recently failed runs BEFORE dispatching. An existing
  // in-progress run may not yet have written its CLAIMED state.
  const runs = await fetchWorkflowRuns(fetcher, c);
  const gate = evaluateRunGate(runs, c.dailyCap, now);
  if (gate !== "CAN_DISPATCH") {
    return { status: gate, reason };
  }
  await dispatchGithub(fetcher, c);
  return { status: "GITHUB_WORKFLOW_DISPATCHED", reason };
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil((async () => {
      try {
        const result = await tick(env);
        console.log("JIRUM_CONDITIONAL_POLL " + JSON.stringify(result));
      } catch (err) {
        // NEVER log bearer tokens, raw Telegram HTML or GitHub state.
        console.error("JIRUM_CONDITIONAL_ERROR " + (err?.message || "UNEXPECTED_ERROR"));
      }
    })());
  },
  // Prevent direct/public execution by URL. Only the Cloudflare cron can run it.
  async fetch() {
    return new Response("Not Found", { status: 404 });
  },
};
