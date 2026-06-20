# Telegram Webhook Worker

接收 Telegram callback（审稿按钮）→ 秒回 toast + 编辑消息 + 写 KV；`GET /decisions` 供 finalize 拉回决策。

## 路由

- `POST /tg` — Telegram webhook。校验 `X-Telegram-Bot-Api-Secret-Token`；解析 `callback_query.data`（`{item_id}:{action}`）→ `answerCallbackQuery` + `editMessageText` → `KV.put(dec:{item_id}, action, TTL 7d)`。任何内部错误回 200（防重投）。
- `GET /decisions` — 校验 `Authorization: Bearer <DECISIONS_API_SECRET>` → 返回 `{item_id: action}`。

## 一次性配置（需 Cloudflare 账号 + Node）

```bash
cd workers/telegram-webhook
npm install
npx wrangler login

# 1) 建 KV namespace, 把输出的 id 回填 wrangler.toml 的 kv_namespaces.id
npx wrangler kv namespace create DECISIONS

# 2) 配 secrets
npx wrangler secret put TELEGRAM_BOT_TOKEN      # 你的 bot token
npx wrangler secret put WEBHOOK_SECRET          # 自拟随机串, 校验 webhook 来源
npx wrangler secret put DECISIONS_API_SECRET    # 自拟随机串, finalize 拉取用

# 3) 部署, 记下 https://<name>.<acct>.workers.dev
npm run deploy

# 4) 注册 webhook
TELEGRAM_BOT_TOKEN=xxx WEBHOOK_SECRET=yyy WORKER_URL=https://<name>.workers.dev ./register.sh
```

## 接 finalize（Plan 1 已就绪，Plan 3 收口）

- GitHub Actions(finalize) 配 secret `DECISIONS_API_SECRET`（与 Worker 同值）。
- `config/delivery.yaml` 设 `decisions_api.url: https://<name>.workers.dev`、`telegram.mode: webhook`。

## 测试

`npm test`（vitest + workers pool，本地 mock 外呼，不触网）。当前 5 个用例全绿。

> 注:`wrangler.toml` 的 `compatibility_date` = 部署时近期日期即可；本地 vitest 的 workerd 仅支持到较早日期，会打回退警告，不影响功能。
