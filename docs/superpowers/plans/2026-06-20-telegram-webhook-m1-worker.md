# M1 Plan 2 — Cloudflare Worker + KV（Telegram webhook 端点）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> **前置:** 需要 Cloudflare 账号 + `wrangler` + Node。用户将在到家后配账号；本 plan 可先写好代码与测试，部署/注册/实测留到账号就绪（Task 5）。

**Goal:** 一个常驻 Cloudflare Worker:接收 Telegram callback（点按钮）→ 校验来源 → 秒回 toast + 编辑消息给可见反馈 → 决策写 KV;并暴露 `GET /decisions` 供 finalize（Plan 1 的 `WorkerDecisionStore`）拉回。

**Architecture:** 单文件 Worker（`workers/telegram-webhook/src/index.js`），两个路由:`POST /tg`（webhook）、`GET /decisions`（鉴权拉取）。KV namespace 绑定 `DECISIONS`，key=`dec:{item_id}`、value=action、TTL 7d。outbound 调 Telegram Bot API。vitest + `@cloudflare/vitest-pool-workers` 单测（mock 外呼）。

**Tech Stack:** Cloudflare Workers, Workers KV, wrangler, vitest, Node 20+。**与现有 Python 仓库并存(monorepo);不进 Python CI。**

**契约对接（来自 Plan 1 / 设计 §4.1）:**
- 卡片 `callback_data` = `"{item_id}:{action}"`，`item_id`=`sha256(link)[:16]`（hex，无冒号），`action`∈`keep|drop|skip`。
- KV:`PUT dec:{item_id} = action`，`expirationTtl: 604800`。
- `GET /decisions` → `{item_id: action}`（Plan 1 `WorkerDecisionStore.fetch()` 期望此形）。
- webhook 校验 header `X-Telegram-Bot-Api-Secret-Token == WEBHOOK_SECRET`；`/decisions` 校验 `Authorization: Bearer {DECISIONS_API_SECRET}`。

---

## 文件结构

- Create: `workers/telegram-webhook/package.json` — wrangler + vitest 依赖、脚本
- Create: `workers/telegram-webhook/wrangler.toml` — Worker 名/入口/KV 绑定
- Create: `workers/telegram-webhook/vitest.config.js` — workers pool 配置
- Create: `workers/telegram-webhook/src/index.js` — Worker（两路由）
- Create: `workers/telegram-webhook/test/index.test.js` — 单测
- Create: `workers/telegram-webhook/register.sh` — setWebhook 注册脚本
- Create: `workers/telegram-webhook/README.md` — 账号/部署/注册/实测步骤
- Create: `workers/telegram-webhook/.gitignore` — `node_modules/`、`.wrangler/`

---

## Task 1: 脚手架（package.json / wrangler.toml / vitest / gitignore）

**Files:** Create the four config files above.

- [ ] **Step 1: 写 `workers/telegram-webhook/package.json`**

```json
{
  "name": "ai-newsday-telegram-webhook",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy",
    "test": "vitest run"
  },
  "devDependencies": {
    "@cloudflare/vitest-pool-workers": "^0.5.0",
    "vitest": "^2.0.0",
    "wrangler": "^3.80.0"
  }
}
```

- [ ] **Step 2: 写 `workers/telegram-webhook/wrangler.toml`**

```toml
name = "ai-newsday-telegram-webhook"
main = "src/index.js"
compatibility_date = "2026-06-01"

# KV: 决策存储。id 由 `wrangler kv namespace create DECISIONS` 生成后回填(Task 5)。
[[kv_namespaces]]
binding = "DECISIONS"
id = "PLACEHOLDER_KV_NAMESPACE_ID"

# Secrets(不写这里, 用 `wrangler secret put`):
#   TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, DECISIONS_API_SECRET
```

- [ ] **Step 3: 写 `workers/telegram-webhook/vitest.config.js`**

```js
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        miniflare: {
          kvNamespaces: ["DECISIONS"],
          bindings: {
            WEBHOOK_SECRET: "test-webhook-secret",
            DECISIONS_API_SECRET: "test-api-secret",
            TELEGRAM_BOT_TOKEN: "test-bot-token",
          },
        },
      },
    },
  },
});
```

- [ ] **Step 4: 写 `workers/telegram-webhook/.gitignore`**

```
node_modules/
.wrangler/
```

- [ ] **Step 5: 安装依赖（需要 Node；可在有网时跑）**

Run: `cd workers/telegram-webhook && npm install`
Expected: 装好 wrangler/vitest（若离线则 Task 5 时补装；不阻塞写代码）。

- [ ] **Step 6: 提交**

```bash
git add workers/telegram-webhook/package.json workers/telegram-webhook/wrangler.toml workers/telegram-webhook/vitest.config.js workers/telegram-webhook/.gitignore
git commit -m "chore(worker): scaffold telegram-webhook (wrangler + vitest)"
```

---

## Task 2: webhook 处理（`POST /tg`）+ 路由骨架

**Files:** Create `src/index.js`; Test `test/index.test.js`.

实现要点:
- `fetch(request, env)` 路由:`POST /tg` → `handleWebhook`；`GET /decisions` → `handleDecisions`（Task 3）；其余 404。
- `handleWebhook`:校验 `X-Telegram-Bot-Api-Secret-Token`，不符 → 403。解析 JSON（失败 → 200 忽略）。取 `update.callback_query`，无则 200 忽略。`data` 用 `indexOf(":")` 切出 `itemId`/`action`（item_id 为 hex 无冒号）。`action` 不在 `keep|drop|skip` → 200 忽略。调 `answerCallbackQuery`（toast=标签）+ `editMessageText`（原文追加状态行）。`KV.put('dec:'+itemId, action, {expirationTtl:604800})`。**任何内部异常都返回 200**（避免 Telegram 无限重投）。

- [ ] **Step 1: 写失败测试 `test/index.test.js`**

```js
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd workers/telegram-webhook && npm test`
Expected: FAIL（`../src/index.js` 不存在）

- [ ] **Step 3: 写 `src/index.js`**

```js
const ACTIONS = { keep: "✅ 已保留", drop: "❌ 已删除", skip: "⏭ 已跳过" };
const TTL = 604800; // 7 天

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/tg") {
      return handleWebhook(request, env);
    }
    if (request.method === "GET" && url.pathname === "/decisions") {
      return handleDecisions(request, env);
    }
    return new Response("not found", { status: 404 });
  },
};

async function tg(env, method, payload) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function handleWebhook(request, env) {
  if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
    return new Response("forbidden", { status: 403 });
  }
  try {
    const update = await request.json();
    const cq = update.callback_query;
    if (cq && typeof cq.data === "string") {
      const i = cq.data.indexOf(":");
      const itemId = i >= 0 ? cq.data.slice(0, i) : "";
      const action = i >= 0 ? cq.data.slice(i + 1) : "";
      if (itemId && ACTIONS[action]) {
        const label = ACTIONS[action];
        await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: label });
        if (cq.message) {
          const old = cq.message.text || "";
          await tg(env, "editMessageText", {
            chat_id: cq.message.chat.id,
            message_id: cq.message.message_id,
            text: `${old}\n\n${label}`,
          });
        }
        await env.DECISIONS.put(`dec:${itemId}`, action, { expirationTtl: TTL });
      }
    }
  } catch (_e) {
    // 吞掉: 任何错误都回 200, 防 Telegram 无限重投
  }
  return new Response("ok");
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd workers/telegram-webhook && npm test`
Expected: PASS（POST /tg 三个用例）。GET /decisions 用例尚未加（Task 3）。

- [ ] **Step 5: 提交**

```bash
git add workers/telegram-webhook/src/index.js workers/telegram-webhook/test/index.test.js
git commit -m "feat(worker): POST /tg webhook — answer+edit+KV write, secret-gated"
```

---

## Task 3: `GET /decisions` 鉴权拉取

**Files:** Modify `src/index.js`（加 `handleDecisions`）；Test `test/index.test.js`（追加）。

- [ ] **Step 1: 追加失败测试**

```js
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd workers/telegram-webhook && npm test`
Expected: FAIL（`/decisions` 当前走不到 handler，404≠预期；或 handler 未定义）

- [ ] **Step 3: 在 `src/index.js` 末尾加 `handleDecisions`**

```js
async function handleDecisions(request, env) {
  if (request.headers.get("Authorization") !== `Bearer ${env.DECISIONS_API_SECRET}`) {
    return new Response("forbidden", { status: 403 });
  }
  const out = {};
  let cursor;
  do {
    const page = await env.DECISIONS.list({ prefix: "dec:", cursor });
    for (const k of page.keys) {
      const action = await env.DECISIONS.get(k.name);
      if (action) out[k.name.slice(4)] = action; // 去掉 "dec:" 前缀
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return Response.json(out);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd workers/telegram-webhook && npm test`
Expected: PASS（全部用例：POST /tg 3 + GET /decisions 2）

- [ ] **Step 5: 提交**

```bash
git add workers/telegram-webhook/src/index.js workers/telegram-webhook/test/index.test.js
git commit -m "feat(worker): GET /decisions — bearer-gated {item_id: action} map"
```

---

## Task 4: register.sh + README

**Files:** Create `register.sh`, `README.md`。

- [ ] **Step 1: 写 `workers/telegram-webhook/register.sh`**

```bash
#!/usr/bin/env bash
# 注册 Telegram webhook 指向已部署的 Worker。
# 需要环境变量: TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, WORKER_URL(例 https://xxx.workers.dev)
set -euo pipefail
: "${TELEGRAM_BOT_TOKEN:?need TELEGRAM_BOT_TOKEN}"
: "${WEBHOOK_SECRET:?need WEBHOOK_SECRET}"
: "${WORKER_URL:?need WORKER_URL}"

curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WORKER_URL%/}/tg" \
  --data-urlencode "secret_token=${WEBHOOK_SECRET}" \
  --data-urlencode 'allowed_updates=["callback_query"]'
echo
echo "已注册。验证: curl https://api.telegram.org/bot\$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

`chmod +x register.sh`。

- [ ] **Step 2: 写 `workers/telegram-webhook/README.md`**

````markdown
# Telegram Webhook Worker

接收 Telegram callback（审稿按钮）→ 秒回 + 写 KV；`GET /decisions` 供 finalize 拉回。

## 一次性配置（需 Cloudflare 账号 + Node）

```bash
cd workers/telegram-webhook
npm install
npx wrangler login

# 1) 建 KV namespace, 把输出的 id 回填 wrangler.toml 的 kv_namespaces.id
npx wrangler kv namespace create DECISIONS

# 2) 配 secrets
npx wrangler secret put TELEGRAM_BOT_TOKEN      # 你的 bot token
npx wrangler secret put WEBHOOK_SECRET          # 自拟随机串, 用于校验 webhook 来源
npx wrangler secret put DECISIONS_API_SECRET    # 自拟随机串, finalize 拉取用

# 3) 部署, 记下 https://<name>.<acct>.workers.dev
npm run deploy

# 4) 注册 webhook
TELEGRAM_BOT_TOKEN=xxx WEBHOOK_SECRET=yyy WORKER_URL=https://<name>.workers.dev ./register.sh
```

## 接 finalize（Plan 1 已就绪）

GitHub Actions(finalize) 配 secret `DECISIONS_API_SECRET`（与 Worker 同值）；
`config/delivery.yaml` 设 `decisions_api.url: https://<name>.workers.dev`、`telegram.mode: webhook`。
→ 见 Plan 3。

## 测试
`npm test`（vitest，本地 mock，不触网）。
````

- [ ] **Step 3: 提交**

```bash
git add workers/telegram-webhook/register.sh workers/telegram-webhook/README.md
git commit -m "docs(worker): register.sh + setup README"
```

---

## Task 5: 部署 + 冒烟（需账号；用户到家后执行，勿在无账号时强跑）

> 非 TDD;真实环境操作。前置:Cloudflare 账号、`wrangler login`、Node。

- [ ] **Step 1:** `npm install`（若 Task1 未装）
- [ ] **Step 2:** `npx wrangler kv namespace create DECISIONS` → 把 id 回填 `wrangler.toml` → 提交该回填。
- [ ] **Step 3:** `wrangler secret put` 三个 secret（见 README）。
- [ ] **Step 4:** `npm run deploy` → 记下 workers.dev URL。
- [ ] **Step 5:** `./register.sh`（设好三个环境变量）→ `getWebhookInfo` 确认 url 正确、`pending_update_count` 合理、无 `last_error_message`。
- [ ] **Step 6 冒烟:** 手动发一张卡片（本地 `uv run python -m src.cli --tick collect`，需 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`），在 Telegram 点按钮 → 确认 **toast 弹出 + 消息追加状态行**（秒级，不再 "Loading…"）。
- [ ] **Step 7:** `curl -H "Authorization: Bearer <DECISIONS_API_SECRET>" https://<name>.workers.dev/decisions` → 确认返回含刚点的 `{item_id: action}`。

> 全部通过 = Worker 侧实测通过。端到端（finalize→Pages 可见）在 Plan 3 收口。

---

## Self-review 结果（写计划时自查）

- **设计覆盖**:§4.1 Worker 两路由=Task2/3;校验 secret/bearer=Task2/3;KV `dec:{item_id}` TTL=Task2;register setWebhook(secret_token+callback_query)=Task4;部署/冒烟=Task5。
- **契约一致**:KV key `dec:{item_id}` ↔ `/decisions` `slice(4)` 去前缀 ↔ Plan 1 `WorkerDecisionStore` 期望 `{item_id: action}`;`callback_data` `indexOf(":")` 切分匹配 Plan 1 卡片 `f"{item_id}:{action}"`;`action` 白名单 `keep|drop|skip` 与 Python 端一致。
- **占位**:`wrangler.toml` 的 KV `id` 是有意 PLACEHOLDER（Task5 回填），README/Task5 已说明;非疏漏。
- **风险**:`@cloudflare/vitest-pool-workers` 的 `fetchMock`/`cloudflare:test` API 版本若与示例不符,按其当前文档微调 import（`fetchMock` 来自 `cloudflare:test`）;不影响 Worker 运行代码。`compatibility_date` 用部署时近期日期即可。
