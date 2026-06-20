# M1 Plan 3 — 可见链 + 激活 webhook + 清理 + 端到端实测

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
> **前置:** Plan 1（已完成）+ Plan 2（Worker 已部署、URL 已知）。本 plan 的实测（Task 5）需要 Worker 在线 + 真 bot/真 chat + GitHub secrets。

**Goal:** 让 finalize 的 content push 真正触发 Pages 部署；把投递切到 webhook 模式接上已部署的 Worker；删掉 Plan 1 之后变 dead 的轮询代码；端到端实测"点击→定稿→Pages 可见"。

**Architecture:** finalize.yml 的 checkout 改用 PAT（使 push 触发 `pages.yml`，绕过 GITHUB_TOKEN 防递归）；`config/delivery.yaml` 切 `mode: webhook` + 填 Worker `decisions_api.url`；移除 `poll_decisions`/`poll_decisions_loop`/`_fetch_once` 及 notifier 的 `db` 依赖；手动实测收口。

**Tech Stack:** GitHub Actions, Hugo Pages, Python（删码 + 测试回归）。

**关键事实:** GitHub 默认 `GITHUB_TOKEN` push 的 commit **不会触发** `pages.yml`（防递归），`gh workflow run`(workflow_dispatch via GITHUB_TOKEN) **同样被挡**。可靠解 = 用 **PAT** 做 checkout token，使后续 `git push` 以 PAT 身份触发下游 workflow。

---

## 文件结构

- Modify: `.github/workflows/finalize.yml` — checkout 加 `token: PAGES_PUSH_TOKEN`
- Modify: `config/delivery.yaml` — `telegram.mode: webhook` + `decisions_api.url`
- Modify: `src/notifiers/__init__.py` — `Notifier` 协议删 `poll_decisions`；`FakeNotifier` 删 `poll_decisions`/`queue_decision`/`_decisions`
- Modify: `src/notifiers/telegram_polling.py` — 删 `poll_decisions`/`poll_decisions_loop`/`_fetch_once`，去掉 `db` 依赖
- Modify: `src/notifiers/website.py` — 删 `poll_decisions`
- Modify: `src/cli.py:384` — `TelegramPollingNotifier(dcfg.telegram)`（去掉 `db=db`）
- Modify: `tests/contract/test_notifier_protocol.py` — 删 `test_fake_notifier_poll_decisions`
- Modify: `tests/contract/test_telegram_notifier.py` — 删 `test_poll_decisions_calls_get_updates`（含 `db=mock_db` 构造）
- Modify: `tests/contract/test_tick_decisions.py` — 删/改 `test_collect_no_longer_polls_decisions`（其依赖的 `queue_decision` 被移除；行为已由"无 poll 方法"结构性保证）
- Create: `docs/runbook-telegram-webhook.md` — secrets/部署/实测速查（可选，便于复跑）

---

## Task 1: finalize.yml 用 PAT 触发 Pages

**Files:** Modify `.github/workflows/finalize.yml`

- [ ] **Step 1: 看现状**

Run: `sed -n '1,40p' .github/workflows/finalize.yml`
确认 `actions/checkout@v4` 步骤当前没有 `token:`，且后面有 `git push` 步骤。

- [ ] **Step 2: 给 checkout 加 PAT token**

把 `- uses: actions/checkout@v4` 改为：

```yaml
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.PAGES_PUSH_TOKEN }}
```

（保持后续 commit/push 步骤不变；push 现在以 PAT 身份发出 → 触发 `pages.yml`。）

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/finalize.yml
git commit -m "ci(finalize): checkout with PAT so content push triggers Pages deploy"
```

> **运行前置（实测时配，见 Task 5）:** 在 repo 建 secret `PAGES_PUSH_TOKEN` = 一个有 `contents:write` + `workflows` 权限的 PAT（classic: `repo` + `workflow`;或 fine-grained 限本 repo: Contents RW + Workflows RW）。

---

## Task 2: 删除 dead 轮询代码

> Plan 1 后 collect 不再轮询、finalize 走 webhook 拉取，`poll_decisions*` 全链路无人调用（终审 Important #2）。整块移除，连带过时测试。

**Files:** 见上「文件结构」中 notifiers/cli/tests 各项。

- [ ] **Step 1: 删 `Notifier` 协议 + FakeNotifier 的轮询面**

在 `src/notifiers/__init__.py`:
- 从 `Notifier(Protocol)` 删除整个 `poll_decisions` 方法声明。
- 从 `FakeNotifier` 删除 `queue_decision`、`poll_decisions`，以及 `__init__` 里的 `self._decisions = []`。保留 `sent_cards` / `final_report` / `send_review_card` / `send_final_report`。

- [ ] **Step 2: 删 TelegramPollingNotifier 的轮询 + db 依赖**

在 `src/notifiers/telegram_polling.py`:
- 删除方法 `_fetch_once`、`poll_decisions`、`poll_decisions_loop`。
- `__init__` 去掉 `db` 参数与 `self._db`（仅 `_fetch_once` 用过它读写 `telegram_offset`）。结果 `__init__(self, config)` 只存 `self._cfg` / `self._bot`。
- 移除因此变成未用的 import（如 `logging`，若别处不再用；用 ruff 确认）。

- [ ] **Step 3: 删 WebsiteNotifier.poll_decisions**

在 `src/notifiers/website.py` 删除 `poll_decisions` 方法（约 line 35）。

- [ ] **Step 4: 改 cli 构造**

`src/cli.py:384`：`tg = TelegramPollingNotifier(dcfg.telegram, db=db)` → `tg = TelegramPollingNotifier(dcfg.telegram)`。（`db` 仍用于 pipeline 其它处，不动。）

- [ ] **Step 5: 删过时测试**

- `tests/contract/test_notifier_protocol.py`：删 `test_fake_notifier_poll_decisions`（依赖 `queue_decision`/`poll_decisions`）。若文件内还有别的有效用例（如 send_review_card 协议测试），保留。
- `tests/contract/test_telegram_notifier.py`：删 `test_poll_decisions_calls_get_updates`（含 `db=mock_db` 构造）。其余 send_review_card / send_final_report / clip / escape 用例保留。
- `tests/contract/test_tick_decisions.py`：删 `test_collect_no_longer_polls_decisions`（其用 `FakeNotifier.queue_decision`，且"collect 不轮询"现已由无 poll 方法结构性保证）。保留 finalize 的两个用例。

- [ ] **Step 6: 全量回归 + lint**

Run: `uv run pytest -q`
Expected: 全绿（用例数比 Plan 1 末态少 3 个删除的）。
Run: `uv run ruff check src tests`
Expected: clean（特别注意 telegram_polling.py 删码后无未用 import；有则删）。

- [ ] **Step 7: 提交**

```bash
git add src/notifiers/ src/cli.py tests/contract/test_notifier_protocol.py tests/contract/test_telegram_notifier.py tests/contract/test_tick_decisions.py
git commit -m "refactor(notifiers): remove dead polling path (webhook supersedes poll_decisions)"
```

> 备注（不在本 plan）:`TelegramPollingNotifier` 名称去掉 polling 后已名不副实，可在独立小 PR 重命名为 `TelegramNotifier`（连带 `telegram_polling.py` → `telegram.py`、cli/测试 import）。本 plan 不做，避免 churn。

---

## Task 3: 切 webhook 模式 + 接 Worker

**Files:** Modify `config/delivery.yaml`

- [ ] **Step 1: 改 delivery.yaml**

把 `telegram.mode` 设为 `webhook`，`decisions_api.url` 填 Plan 2 部署得到的 Worker URL：

```yaml
telegram:
  bot_token: ""
  chat_id: ""
  mode: "webhook"        # 由 polling 改为 webhook

decisions_api:
  url: "https://ai-newsday-telegram-webhook.<acct>.workers.dev"   # 填真实 Worker URL
  # secret 通过 DECISIONS_API_SECRET 环境变量注入
```

- [ ] **Step 2: 验证配置可加载**

Run: `uv run python -c "from src.core.config import load_delivery_config; c=load_delivery_config('config/delivery.yaml'); print(c.telegram.mode, c.decisions_api.url)"`
Expected: 打印 `webhook https://...workers.dev`（`decision_store` 仅在 `DECISIONS_API_SECRET` 也存在时才在 cli 里被构造）。

- [ ] **Step 3: 提交**

```bash
git add config/delivery.yaml
git commit -m "config(delivery): switch to webhook mode + wire Worker decisions_api url"
```

> **运行前置（Task 5 配）:** GitHub Actions 的 collect.yml 与 finalize.yml 需要 secret `DECISIONS_API_SECRET`（与 Worker 同值）注入到环境。finalize 拉取决策、collect 仅发卡片（不需要，但无害）。在 finalize.yml 的 `env:` 段加 `DECISIONS_API_SECRET: ${{ secrets.DECISIONS_API_SECRET }}`。

- [ ] **Step 4: 给 finalize.yml 注入 DECISIONS_API_SECRET**

在 `.github/workflows/finalize.yml` 的 `env:` 段（已有 `TELEGRAM_BOT_TOKEN` 等处）加一行：
```yaml
      DECISIONS_API_SECRET: ${{ secrets.DECISIONS_API_SECRET }}
```
提交：
```bash
git add .github/workflows/finalize.yml
git commit -m "ci(finalize): inject DECISIONS_API_SECRET for decision pull"
```

---

## Task 4: 合并 M1（三 plan 一起进 master）

> ⚠️ Plan 1 已停 collect 轮询;Plan 1+2+3 必须一起合并，否则 webhook 激活前决策无人收集。

- [ ] **Step 1:** 确认 branch 含 Plan1+2+3 全部 commit，`uv run pytest -q` 全绿，Worker `npm test` 全绿。
- [ ] **Step 2:** 用 `superpowers:finishing-a-development-branch` 收口（开 PR 或合并）。PR 描述写明:实现 M1（webhook 人审闭环 + 可见链）、新增测试、Worker 部署说明、**实测结果（Task 5）**。
- [ ] **Step 3:** 合并前确保 CI（test.yml/lint.yml）绿。

---

## Task 5: 端到端实测（需账号 + Worker 在线 + secrets；用户操作）

> 非自动化;真实链路验证。前置:Plan 2 Worker 已部署 + webhook 已注册;repo secrets `PAGES_PUSH_TOKEN`、`DECISIONS_API_SECRET`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`MODELSCOPE_API_KEY` 就绪;GitHub Pages 已启用。

- [ ] **Step 1 发卡片:** 触发或等 collect（`collect.yml`）跑一轮 → Telegram 收到审稿卡片（带按钮）。
- [ ] **Step 2 点击秒回:** 点某条「留/删/跳」→ **确认 toast 弹出 + 消息追加状态行**（秒级，不再 "Loading…"）。
- [ ] **Step 3 决策落库验证:** `curl -H "Authorization: Bearer <DECISIONS_API_SECRET>" <WORKER_URL>/decisions` → 返回含刚点的 `{item_id: action}`。
- [ ] **Step 4 定稿:** 手动触发 `finalize.yml`（workflow_dispatch）→ 看 Actions 日志:`decisions_fetch_error` 不应出现;被 drop 的条目不进终稿。
- [ ] **Step 5 可见链:** finalize 提交 `content/posts/<date>.md` 后，**确认 `pages.yml` 被自动触发并部署成功**（这是 PAT 修复的关键验证点）→ 打开 `https://ai-newsday.github.io/core/` 看到当天日报。
- [ ] **Step 6 终稿推送:** 确认 Telegram 收到终稿「简报 + 阅读全文链接」，**不截断、无 parse 错误**，链接指向当天 post。
- [ ] **Step 7 记录:** 把实测结果（截图/日志要点）写入 PR 描述或 `docs/runbook-telegram-webhook.md`。

> 全部通过 = M1 闭环实测达成。失败定位:webhook 不回 → 查 `getWebhookInfo` 的 `last_error_message` + Worker secret;Pages 不重建 → 查 PAT scope + finalize push 是否以 PAT 身份;决策没生效 → 查 item_id 一致性（卡片 callback_data vs KV key）。

---

## Self-review 结果（写计划时自查）

- **设计覆盖**:§4.6 可见链 PAT=Task1;§4.2 收尾(删 poll)=Task2;§5 配置激活=Task3;§7 端到端实测=Task5;合并约束=Task4。
- **dead-code 牵连已核实（grep）**:`poll_decisions` 在 website/telegram_polling/__init__(协议+Fake);`_fetch_once`+`db` 仅 telegram_polling 用;测试 3 处（test_notifier_protocol、test_telegram_notifier、test_tick_decisions 的 queue_decision）——均在 Task2 Step5 处理。
- **占位**:`decisions_api.url` 与 PAT/secret 是真实环境值，Task3/Task5 标注"实测时填",非疏漏。
- **风险**:删 `db` 参数后，若除测试外还有别处用 `TelegramPollingNotifier(..., db=...)`——已 grep 确认只有 cli:384 与被删测试;执行时再 `grep -rn "TelegramPollingNotifier(" ` 复核一次。`finishing-a-development-branch`(Task4) 需在本仓库实际有该 skill 时调用。
