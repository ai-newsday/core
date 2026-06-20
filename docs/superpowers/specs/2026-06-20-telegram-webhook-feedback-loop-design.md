# M1 设计 — Telegram webhook 人审闭环 + 可见链

- 日期:2026-06-20
- 来源:interview-me → brainstorm（用户确认拓扑 + 决策 a）
- 意图:`docs/intent/telegram-feedback-loop-and-visibility.md`
- 状态:设计待用户复审 → writing-plans

## 1. 目标 / 验收（必须实测 end-to-end）

把"在 Telegram 点按钮 → 决策落库 → finalize 定稿 → Pages 可见"这条**异步人审闭环**真正打通并**实测通过**:

1. 点卡片按钮 **< 2s** 内 Telegram 有可见反馈（toast + 消息追加"已保留/已删除/已跳过"）。
2. 该决策被持久化,finalize 能读到并应用（kept/dropped 真实影响终稿)。
3. 触发 finalize → 产出 `content/posts/<date>.md` → **Pages 自动重建** → 打开 `https://ai-newsday.github.io/core/` 看到当天终稿。
4. 终稿 Telegram 推送不截断、不报 4096 / parse 错误。

> 本设计**不含**日报文风/版式重做（M2,见 `references/editorial-and-format-sop.md`)。卡片/终稿用现有内容字段,仅修发送 bug;内容契约改造留 M2。

## 2. 根因回顾

- 轮询写死在 `run_collect_tick` 同步阻塞 120s(`poll_decisions_loop`)。人工审阅异步:cron 跑时用户不在场(`0/6 decided`);用户后点击时无在线消费者 → 永远 "Loading…"、不落库。
- `finalize` 是 `workflow_dispatch` 从没触发过 → 终稿从未产出。
- finalize 的 git push 用默认 `GITHUB_TOKEN` → **不触发** `pages.yml`(GitHub 防递归)→ 站停在 06-17。
- 发送 bug:`send_final_report` `body=markdown[:3800]` 盲截;卡片 body 不设上限超 4096 报错。

## 3. 架构（拓扑已确认）

```
collect tick ──发卡片(callback_data=item_id:action)──► Telegram
                                                          │ 用户点按钮
                                                          ▼
        Cloudflare Worker (常驻 HTTPS, 注册为 Telegram webhook)
          ① 校验 X-Telegram-Bot-Api-Secret-Token
          ② answerCallbackQuery(toast) + editMessageText(追加状态)   ← 秒级可见反馈
          ③ 写 CF KV: dec:{item_id} = action  (TTL 7d)
          ④ GET /decisions  (Authorization: Bearer <secret>) → {item_id: action}
                                                          ▲ HTTP GET
finalize tick ──拉决策→并入 state.db→review→publish→push(PAT)──► pages.yml 重建 ──► URL 可见
```

webhook 与 `getUpdates` **互斥**:启用 webhook 后轮询路径作废。

## 4. 组件

### 4.1 Cloudflare Worker `workers/telegram-webhook/`（新增,放本仓库 monorepo）
- 运行时:CF Workers + KV;`wrangler.toml` 声明 KV namespace 绑定 `DECISIONS`。
- Secrets(wrangler secret):`TELEGRAM_BOT_TOKEN`、`WEBHOOK_SECRET`(Telegram secret_token)、`DECISIONS_API_SECRET`(供 finalize 拉取鉴权)。
- 路由:
  - `POST /tg`(webhook):校验 secret → 解析 `callback_query` → `callback_data` 拆 `item_id:action` → 调 Telegram `answerCallbackQuery`(text=已保留/已删除/已跳过)+ `editMessageText`(原文追加状态行) → `KV.put('dec:'+item_id, action, {expirationTtl: 604800})`。`item_id`=`sha256(link)[:16]` 全局唯一,无需 date。
  - `GET /decisions`:校验 `Authorization` → `KV.list({prefix:'dec:'})` → 返回 `{item_id: action}` JSON(近 7d 内所有决策)。
- 非 callback 更新一律 200 忽略。失败对 Telegram 返回 200(避免无限重投)。

### 4.2 collect tick 改动（`src/pipeline/tick.py`）
- **删除** `poll_decisions_loop` / `poll_decisions` 那段收决策逻辑;collect 只发卡片、写 `pending_review`、退出。
- 卡片发送逻辑不变(仍 `send_review_card`,callback_data 不变)。

### 4.3 finalize tick 改动（`src/pipeline/tick.py`）
- review 前新增:`GET {WORKER_URL}/decisions`(Bearer `DECISIONS_API_SECRET`) → 得 `{item_id: action}`(全量)。
- 只取**当天 pending item_ids** 与之的交集,**幂等**并入 `state.db`(复用 `db.update_decision(item_id, action)`),再走现有 `get_decisions_dict(date)` → review。
- 拉取失败(Worker 不可达):记 `decisions_fetch_error` 事件,降级用 DB 已有决策(未审默认 keep),**不致命**。

### 4.4 决策读取适配器（新增,隔离 IO）
- `src/adapters/decisions/worker.py`:`WorkerDecisionStore.fetch(date) -> dict[item_id,action]`。纯 HTTP,可注入 Fake 做测试。配置走 `config/delivery.yaml` 新增 `decisions_api: {url, secret_env}`。

### 4.5 发送 bug 修复（`src/notifiers/telegram_polling.py`）
- **终稿**:不再 `<pre>{markdown[:3800]}</pre>` 盲截。改为**简报 + 链接**:报头(日期/条数/必读数)+ 必读标题清单 + 指向 Pages URL 的链接。彻底规避 4096 截断,也更专业(契合 M2 方向)。
- **卡片**:`body` 发送前做 4096 安全截断(留省略号);确认 `parse_mode=HTML` 下所有插值已 `html.escape`(锚点 URL 用属性转义)。
- 这些是纯函数化的文本组装,可 golden 测试。

### 4.6 可见链 S1（`.github/workflows/finalize.yml`）
- finalize 的 `git push` 改用 **PAT**(secret `PAGES_PUSH_TOKEN`)而非默认 `GITHUB_TOKEN`,使 content 变更触发 `pages.yml`。
- 备选(若不想加 PAT):finalize.yml 末尾 `gh workflow run pages.yml` 显式触发。**采用 PAT 方案**(最少改动、语义最干净)。

## 5. 配置 / Secrets 汇总

| 处 | 名称 | 用途 |
|---|---|---|
| CF Worker | `TELEGRAM_BOT_TOKEN` | 调 Telegram API |
| CF Worker | `WEBHOOK_SECRET` | 校验 webhook 来源 |
| CF Worker | `DECISIONS_API_SECRET` | /decisions 鉴权 |
| GitHub Actions(finalize) | `DECISIONS_API_SECRET` | 拉决策 |
| GitHub Actions(finalize) | `PAGES_PUSH_TOKEN`(PAT) | push 触发 pages |
| `config/delivery.yaml` | `telegram.mode: webhook` + `decisions_api.url` | 切 webhook 模式 + Worker 地址 |

setWebhook 一次性注册(脚本 `workers/telegram-webhook/register.sh`:调 `setWebhook` 带 url+secret_token+allowed_updates=["callback_query"])。

## 6. 失败模式

| 场景 | 行为 |
|---|---|
| Worker 宕机 | 点击无反馈(Telegram 重投);finalize 拉取失败 → 降级 DB 决策,不致命 |
| KV 写失败 | Worker 返回 200,该决策丢失(可重点;TTL 兜底) |
| finalize 无决策 | 现有逻辑:未审默认 keep |
| 重复点击同条 | KV 覆盖为最新 action;finalize 幂等 |

## 7. 测试策略

- **Python 侧 TDD**:collect tick 去轮询(契约:不再调 poll);finalize 拉取并入(注入 `FakeDecisionStore`,验证决策正确并入 + 拉取失败降级);发送组装(golden:终稿简报+链接、卡片 4096 截断、HTML 转义)。
- **Worker 侧**:`wrangler` 本地单测(callback 解析、secret 校验、KV 读写、/decisions 鉴权)。
- **端到端实测(用户要求,手动)**:部署 Worker → register.sh 注册 webhook → 本地/Actions 跑 collect 发卡片 → 真机点按钮(验 toast+消息追加+KV 有值)→ 触发 finalize(验决策应用 + content 产出 + Pages 重建 + URL 可见 + 终稿推送正常)。

## 8. Out of scope

- 日报文风/版式/内容契约重做(M2,`references/editorial-and-format-sop.md`)。
- 配额/内容过滤(甲-3)。GitHub 源(子项目2)。Reddit 救活。

## 9. 实现拆分（writing-plans 细化）

1. 发送 bug 修复（纯文本组装,最独立,先红后绿）。
2. collect 去轮询 + finalize 拉取并入 + 决策适配器（Python,TDD）。
3. Cloudflare Worker + KV + register 脚本（JS/wrangler）。
4. finalize.yml PAT 触发 pages（workflow）。
5. 端到端实测 + 调通。
