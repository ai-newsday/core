import { env, fetchMock } from "cloudflare:test";
import { beforeAll, afterEach, describe, it, expect } from "vitest";
import worker from "../src/index.js";

beforeAll(() => { fetchMock.activate(); fetchMock.disableNetConnect(); });
afterEach(() => { fetchMock.assertNoPendingInterceptors(); });

function tgOk(method) {
  fetchMock.get("https://api.telegram.org")
    .intercept({ path: `/bottest-bot-token/${method}`, method: "POST" })
    .reply(200, { ok: true });
}

describe("POST /tg", () => {
  it("rejects wrong secret with 403", async () => {
    const req = new Request("https://w/tg", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": "WRONG" },
      body: JSON.stringify({}),
    });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(403);
  });

  it("on callback: answers, edits, writes KV", async () => {
    tgOk("answerCallbackQuery");
    tgOk("editMessageText");
    const update = {
      callback_query: {
        id: "cbid",
        data: "abc123def456:keep",
        message: { message_id: 9, chat: { id: 555 }, text: "卡片正文" },
      },
    };
    const req = new Request("https://w/tg", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret" },
      body: JSON.stringify(update),
    });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    expect(await env.DECISIONS.get("dec:abc123def456")).toBe("keep");
  });

  it("ignores non-callback update with 200", async () => {
    const req = new Request("https://w/tg", {
      method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret" },
      body: JSON.stringify({ message: { text: "hi" } }),
    });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
  });
});

describe("GET /decisions", () => {
  it("rejects wrong bearer with 403", async () => {
    const req = new Request("https://w/decisions", {
      headers: { Authorization: "Bearer WRONG" },
    });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(403);
  });

  it("returns {item_id: action} map for stored decisions", async () => {
    await env.DECISIONS.put("dec:aaa", "keep", { expirationTtl: 604800 });
    await env.DECISIONS.put("dec:bbb", "drop", { expirationTtl: 604800 });
    const req = new Request("https://w/decisions", {
      headers: { Authorization: "Bearer test-api-secret" },
    });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.aaa).toBe("keep");
    expect(body.bbb).toBe("drop");
  });
});
