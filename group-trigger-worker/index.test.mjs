import test from "node:test";
import assert from "node:assert/strict";
import {
  GH_DISPATCH_URL, isCoupangUrl, containsCoupang, acceptedUpdate,
  handleWebhook, dispatchBatch,
} from "./index.mjs";

const ENV = {
  MUSE_GROUP_CHAT_ID: "-5254258160",
  SOURCE_SENDER_BOT_ID: "987654321",
  TELEGRAM_WEBHOOK_SECRET: "long-sample-secret",
  GITHUB_ACTIONS_TOKEN: "fake-pat",
};
const from = { id: 987654321, is_bot: true };
function update(overrides = {}) {
  return {
    update_id: 5001,
    message: {
      message_id: 244,
      chat: { id: -5254258160, type: "supergroup" },
      from,
      text: "🔥 테스트 핫딜 https://link.coupang.com/a/sample",
      ...overrides,
    },
  };
}
function request(data, secret = ENV.TELEGRAM_WEBHOOK_SECRET) {
  return new Request("https://example.workers.dev/telegram-webhook", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Bot-Api-Secret-Token": secret,
    },
    body: JSON.stringify(data),
  });
}
function createEnv() {
  const sent = [];
  return { sent, env: {
    ...ENV,
    EVENT_QUEUE: { async send(item) { sent.push(item); } },
  } };
}

test("exact Coupang host is accepted, lookalike or HTTP host is rejected", () => {
  for (const val of [
    "https://link.coupang.com/a/xyz?x=1",
    "https://www.coupang.com/vp/products/123",
    "https://m.coupang.com/vp/products/123",
    "https://coupang.com/vp/products/123).",
  ]) assert.equal(isCoupangUrl(val), true, val);
  for (const val of [
    "https://link.coupang.com.evil.example/x",
    "https://coupang.com@evil.example/x",
    "http://link.coupang.com/a/x",
    "https://toss.im/deals/coupang.com",
    "not-an-url",
  ]) assert.equal(isCoupangUrl(val), false, val);
});

test("detect both raw text and hidden Telegram text_link, including captions", () => {
  assert.equal(containsCoupang({ text: "구매 https://link.coupang.com/a/123" }), true);
  assert.equal(containsCoupang({ text: "구매하기", entities: [
    { type: "text_link", url: "https://link.coupang.com/a/hidden" },
  ] }), true);
  assert.equal(containsCoupang({ caption: "쿠팡", caption_entities: [
    { type: "text_link", url: "https://link.coupang.com/a/caption" },
  ] }), true);
  assert.equal(containsCoupang({ text: "http://evil.example" }), false);
});

test("only original sender bot in exact MUSE group accepted", () => {
  const valid = update();
  assert.ok(acceptedUpdate(valid, ENV));
  assert.equal(acceptedUpdate(update({ from: { id: 55, is_bot: true } }), ENV), null);
  assert.equal(acceptedUpdate(update({ from: { id: 987654321, is_bot: false } }), ENV), null);
  assert.equal(acceptedUpdate(update({ chat: { id: -1 } }), ENV), null);
  assert.equal(acceptedUpdate(update({ text: "일반 공지" }), ENV), null);
  assert.equal(acceptedUpdate({ edited_message: valid.message, update_id: 5002 }, ENV).messageId, 244);
  assert.equal(acceptedUpdate({ message: valid.message, update_id: null }, ENV), null);
});

test("webhook only acknowledges an authenticated event after enqueue", async () => {
  const { env, sent } = createEnv();
  const res = await handleWebhook(request(update()), env);
  assert.equal(res.status, 200);
  assert.deepEqual(sent, [{
    updateId: 5001, messageId: 244, group: "-5254258160",
  }]);
});

test("rejects unauthenticated webhook, wrong route, absent sender id", async () => {
  const { env, sent } = createEnv();
  assert.equal((await handleWebhook(request(update(), "wrong"), env)).status, 403);
  const get = new Request("https://example.workers.dev/telegram-webhook");
  assert.equal((await handleWebhook(get, env)).status, 404);
  assert.equal((await handleWebhook(request(update()), {
    ...env, SOURCE_SENDER_BOT_ID: "0",
  })).status, 503);
  assert.equal(sent.length, 0);
});

test("ignores other bots, shopping sites, and humans without invoking queue", async () => {
  const { env, sent } = createEnv();
  const cases = [
    update({ text: "https://toss.im/xyz" }),
    update({ from: { id: 123, is_bot: true } }),
    update({ from: { id: 987654321, is_bot: false } }),
    update({ chat: { id: -8888 } }),
  ];
  for (const item of cases) {
    const res = await handleWebhook(request(item), env);
    assert.equal(res.status, 200);
  }
  assert.equal(sent.length, 0);
});

test("queue outage returns retryable response so Telegram does not lose event", async () => {
  const { env } = createEnv();
  env.EVENT_QUEUE.send = async () => { throw Error("offline"); };
  assert.equal((await handleWebhook(request(update()), env)).status, 503);
});

test("single batch invokes ONE GitHub Actions workflow even with several posts", async () => {
  const calls = [];
  const batch = {
    messages: [{ body: { messageId: 1 } }, { body: { messageId: 2 } }],
    retryAll: () => assert.fail("should not retry success"),
  };
  const fakeFetch = async (url, opts) => {
    calls.push({ url, opts });
    return { status: 204 };
  };
  await dispatchBatch(batch, ENV, fakeFetch);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, GH_DISPATCH_URL);
  assert.equal(calls[0].opts.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].opts.body), { ref: "main" });
  assert.equal(calls[0].opts.headers.Authorization, "Bearer fake-pat");
});

test("GitHub denies or network fails: retry queue instead of losing posts", async () => {
  for (const status of [401, 403, 429, 500]) {
    const retries = [];
    await dispatchBatch({
      messages: [{ body: { messageId: 1 } }],
      retryAll: (opts) => retries.push(opts),
    }, ENV, async () => ({ status }));
    assert.deepEqual(retries, [{ delaySeconds: status === 429 ? 300 : 180 }]);
  }
  const retries = [];
  await dispatchBatch({
    messages: [{ body: { messageId: 1 } }],
    retryAll: (opts) => retries.push(opts),
  }, ENV, async () => { throw new Error("network offline"); });
  assert.deepEqual(retries, [{ delaySeconds: 180 }]);
});

test("missing GitHub token cannot acknowledge a queued batch", async () => {
  const retries = [];
  await dispatchBatch({
    messages: [{}],
    retryAll: (opts) => retries.push(opts),
  }, { ...ENV, GITHUB_ACTIONS_TOKEN: "" });
  assert.deepEqual(retries, [{ delaySeconds: 180 }]);
});
