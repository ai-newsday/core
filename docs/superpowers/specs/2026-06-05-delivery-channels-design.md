# Spec — 发布通道（Delivery Channels）

> 路径：`docs/superpowers/specs/2026-06-05-delivery-channels-design.md`
> 目标：给七层流水线加"人在回路"审稿 + 多通道发布，让日报真正跑起来。

---

## 1. 现状与问题

七层流水线已经跑通（`--dry-run --publish` 有输出），但：

| 问题 | 根因 |
|---|---|
| 每次都是 `is_pending=True`，报头永远有水印 | `decisions.json` 从没人填，review 层默认全 keep + 待审 |
| 每天看日报要手动跑命令 + 看 stdout | 没有定时触发 + 没有推送通道 |
| 状态散落在多个 JSON 文件 | `decisions.json` / `feedback_events.json` / `quality_weights.json` 无法查询历史 |

**这个 spec 解决这三个问题**：定时触发 → Telegram 推审稿卡片 → 你在手机上点按钮 → 22:00 定稿 → 推邮件 / 写网站。

---

## 2. 目标（P1 验收标准）

- ✅ 每天 12:00 / 16:00 / 20:00 自动抓取 + 推送候选卡片到 Telegram
- ✅ 你在手机上对每条点 **[✅ 留] [❌ 删] [⏭ 跳]**
- ✅ 22:00 自动定稿：已审的按决策走，未审的默认 keep + "未审"水印
- ✅ 定稿后把日报写入 `docs/daily/YYYY-MM-DD.md`（本地 / GitHub Pages）
- ✅ 支持两种运行方式：**A = 本地 mac/VPS**，**B = GitHub Actions**（同套代码，换配置）

P2 加：Telegram Webhook（不用常驻进程） + GitHub Actions 自动触发
P3 加：邮件发送（SMTP）+ git push 到 GitHub Pages

---

## 3. 整体架构

```
每日三次 tick (12/16/20 点)          22:00 finalize
─────────────────────────           ──────────────────────
collect()                           读 SQLite pending_reviews
  ↓                                   ↓
enrich_with_hn()                    review(decisions)
  ↓                                   ↓
dedup() → interpret()               publish()
  ↓                                   ↓
写 pending_reviews (SQLite)         send_final_report()
  ↓                                   ├─ WebsiteNotifier → docs/daily/
Telegram 推卡片 ←──── 你点按钮 ─────── └─ EmailNotifier → 邮箱(P3)
  ↓ 决策写回 SQLite
```

### 两种运行方式对比

| | 方案 A：本地 polling | 方案 B：GH Actions + Webhook |
|---|---|---|
| 进程模型 | `TelegramPollingNotifier`，常驻进程 long-poll | `TelegramWebhookNotifier`，无状态，靠 serverless 回调 |
| 调度 | macOS launchd / crontab | GitHub Actions cron |
| 状态存储 | `data/state.db`（本地 SQLite） | GH Actions cache（当天内有效）|
| 前置条件 | mac 开着 / 有 VPS | GitHub Actions 免费额度 + 一个 Webhook 服务（Cloudflare Worker 等） |
| 适合场景 | 调试 / 个人自用起步 | 离线自动化 / 长期稳定运行 |

**两种方式共享同一套 Pipeline 和 Notifier 协议**，只换 `config/delivery.yaml` 里的 `telegram.mode`。

---

## 4. SQLite 状态层（替代现有 JSON 文件）

文件：`src/state/db.py`，用 `aiosqlite`（异步，不阻塞 asyncio 事件循环）。

### 4.1 表结构

```sql
-- 每次 tick 的运行记录
CREATE TABLE IF NOT EXISTS runs (
    run_id  TEXT PRIMARY KEY,
    tick    TEXT NOT NULL,   -- 'collect' | 'finalize'
    ts      TEXT NOT NULL,   -- ISO 时间
    status  TEXT NOT NULL,   -- 'running' | 'done' | 'error'
    notes   TEXT
);

-- 每条候选条目的审稿状态（替代 decisions.json）
CREATE TABLE IF NOT EXISTS pending_reviews (
    item_id    TEXT PRIMARY KEY,   -- sha256(link)[:16]，唯一标识
    run_id     TEXT NOT NULL,
    link       TEXT NOT NULL,
    source     TEXT NOT NULL,
    title_en   TEXT NOT NULL,
    title_zh   TEXT,               -- LLM 解读后的中文标题
    summary_zh TEXT,               -- 中文摘要
    takeaway   TEXT,               -- 对你
    hot_take   TEXT,               -- 锐评
    score      INTEGER,
    signals    TEXT,               -- JSON blob: upvotes/hn_points/likes...
    msg_id     INTEGER,            -- Telegram message_id（发卡片后记录）
    status     TEXT DEFAULT 'pending',  -- 'pending'|'keep'|'drop'|'skip'
    decided_at TEXT,
    date       TEXT NOT NULL,      -- YYYY-MM-DD，用于按天查询
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

-- 反馈事件（替代 feedback_events.json）
CREATE TABLE IF NOT EXISTS feedback_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    link    TEXT NOT NULL,
    source  TEXT NOT NULL,
    action  TEXT NOT NULL,  -- 'keep'|'drop'|'edit'
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL
);

-- 源信誉权重（替代 quality_weights.json）
CREATE TABLE IF NOT EXISTS quality_weights (
    source     TEXT PRIMARY KEY,
    weight     REAL NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.2 关键业务逻辑

| 场景 | SQL |
|---|---|
| 今天已发过这条，不重复推 | `WHERE item_id=? AND date=?` 存在则跳过 |
| 查今天所有未审条目 | `WHERE date=? AND status='pending'` |
| finalize 时统计决策 | `GROUP BY status WHERE date=?` |
| skip = 下次 tick 重新推 | 不更新 status，下次 tick 会再推一次 |

---

## 5. Notifier 协议

```python
# src/notifiers/__init__.py
from typing import Protocol

class Notifier(Protocol):
    async def send_review_card(self, item_id: str, card: dict) -> int | None:
        """推一张审稿卡片。返回平台 message_id（用于后续追踪）。
        不支持交互的通道（邮件等）返回 None。"""
        ...

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        """发送定稿日报。summary = {date_label, item_count, must_read_count}。"""
        ...

    async def poll_decisions(self) -> list[tuple[str, str]]:
        """取出待处理的决策队列（item_id, action）。
        polling 模式：从内存队列取；webhook 模式：返回 [] (决策已经通过 HTTP 写入 DB)。"""
        ...
```

---

## 6. 审稿卡片格式

每条候选推一张这样的 Telegram 消息：

```
[模型] DeepSeek-V4-Pro 发布
DeepSeek-V4-Pro

📊 92分  ｜  👍 4,622 likes  ｜  🔥 HN 12
🔗 hf-models

💬 一句话：DeepSeek 推出新一代旗舰模型 V4-Pro，性能显著提升。
🛠 对你：可替换现有 API 调用，推理速度更快成本更低。
⚡️ 锐评：国产模型继续卷，护城河越来越薄。

[✅ 留]  [❌ 删]  [⏭ 跳过]
```

- **留** = `keep`，写入决策
- **删** = `drop`，写入决策
- **跳过** = 不决策，下次 tick 再看

> v1 不做卡片内编辑（edit）。确实要改文案 → 先删，下轮重新生成。

---

## 7. 两个 tick 的流程

### 7.1 collect tick（12:00 / 16:00 / 20:00）

```
1. collect() + enrich_with_hn()
2. dedup() → interpret()
3. 每条 item：
   a. item_id = sha256(link)[:16]
   b. 如果今天 pending_reviews 里没有这条 → INSERT（status='pending'）
   c. 调 notifier.send_review_card() → 记录 msg_id
4. 调 notifier.poll_decisions() → UPDATE pending_reviews SET status=action
5. emit 'tick_collect_done'
```

### 7.2 finalize tick（22:00）

```
1. 查 pending_reviews WHERE date=today
2. status='pending' 或 'skip' → 默认 action='keep'（未审不拦）
3. 组装 decisions dict
4. review(interpreted_items, decisions) → ReviewResult
5. publish(review_result) → PublishResult
6. notifier.send_final_report(markdown, summary)
   ├─ WebsiteNotifier: 写 docs/daily/YYYY-MM-DD.md
   └─ EmailNotifier: 发邮件（P3 再做）
7. 把本次审稿决策写入 feedback_events
8. 更新 quality_weights
9. emit 'tick_finalize_done'
```

---

## 8. 配置文件

`config/delivery.yaml`（敏感字段通过环境变量覆盖）：

```yaml
telegram:
  bot_token: ""              # 通过 TELEGRAM_BOT_TOKEN 环境变量注入
  chat_id: ""                # 通过 TELEGRAM_CHAT_ID 环境变量注入
  mode: "polling"            # "polling"（本地）或 "webhook"（云端）
  webhook_url: ""            # mode=webhook 时填，如 https://your-worker.dev

email:
  enabled: false
  smtp_host: "smtp.gmail.com"
  smtp_port: 465
  username: ""
  password: ""               # 通过 EMAIL_PASSWORD 环境变量注入
  to: ""

website:
  enabled: true
  output_dir: "docs/daily"
  git_push: false            # true = finalize 后自动 git add + commit + push

schedule:
  collect_ticks: ["12:00", "16:00", "20:00"]
  finalize_tick:  "22:00"
  timezone: "Asia/Shanghai"
```

---

## 9. 调度配置

### 方案 A：macOS launchd（`launchd/ai-newsday.plist`）

```xml
<plist version="1.0"><dict>
  <key>Label</key><string>ai.newsday.collect</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/repo/.venv/bin/python</string>
    <string>-m</string><string>src.cli</string>
    <string>--tick</string><string>collect</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>TELEGRAM_BOT_TOKEN</key><string>替换为真实 token</string>
    <key>TELEGRAM_CHAT_ID</key><string>替换为你的 chat_id</string>
    <key>MODELSCOPE_API_KEY</key><string>替换为 API key</string>
  </dict>
</dict></plist>
```

### 方案 B：GitHub Actions（`.github/workflows/daily.yml`）

```yaml
name: AI Daily

on:
  schedule:
    - cron: "0 4 * * *"    # 12:00 北京时间
    - cron: "0 8 * * *"    # 16:00 北京时间
    - cron: "0 12 * * *"   # 20:00 北京时间
    - cron: "0 14 * * *"   # 22:00 北京时间（定稿）
  workflow_dispatch:
    inputs:
      tick:
        description: "collect 或 finalize"
        default: "collect"

jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv sync
      - name: 恢复当天 SQLite 状态
        uses: actions/cache@v4
        with:
          path: data/state.db
          key: state-${{ github.run_id }}
          restore-keys: state-
      - name: 运行 tick
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          MODELSCOPE_API_KEY: ${{ secrets.MODELSCOPE_API_KEY }}
        run: uv run python -m src.cli --tick ${{ github.event.inputs.tick || 'collect' }}
      - name: 提交日报（仅 finalize）
        if: github.event.inputs.tick == 'finalize'
        run: |
          git config user.name "AI Newsday Bot"
          git config user.email "bot@ai-newsday"
          git add docs/daily/ || true
          git diff --staged --quiet || git commit -m "daily: $(date +%Y-%m-%d)"
          git push
```

---

## 10. 错误处理

| 场景 | 处理方式 |
|---|---|
| Telegram 发卡片失败 | 记录日志，跳过该条（不崩 tick） |
| 22:00 到了还没审 | 全部默认 keep + "未审"水印（PRD §3.4 原有逻辑） |
| SMTP 失败 | 记录日志，继续写网站文件 |
| git push 失败 | 记录日志，本地文件仍已写入 |
| SQLite 被锁 | 重试 3 次，间隔 1s |
| interpret 全部 fallback | 仍然 finalize，用抽取式摘要 |

---

## 11. 测试要求

| 类型 | 测什么 |
|---|---|
| contract | `DeliveryConfig` schema；DB 建表不报错；`Notifier` 协议三个方法签名 |
| unit | `TelegramPollingNotifier.send_review_card` 用 mock bot；`WebsiteNotifier` 写文件 |
| golden | `run_collect_tick` 用 `FakeNotifier`（记录发了几张卡）；`run_finalize_tick` 预填 decisions 跑全链路 |
| 手工验收 | 本地启动 → Telegram 收到卡片 → 点按钮 → 22:00 定稿 → `docs/daily/` 有文件 |

---

## 12. 分阶段计划

| 阶段 | 做什么 | 验收标准 |
|---|---|---|
| **P1（先做）** | SQLite 状态层 + `TelegramPollingNotifier` + `WebsiteNotifier` + `run_collect_tick` + `run_finalize_tick` + launchd 配置 | 本地：手机收到卡片，点按钮，22:00 `docs/daily/` 出现日报 |
| **P2** | `TelegramWebhookNotifier` + GH Actions workflow + Cloudflare Worker stub | 离开 mac 也能跑，Actions log 可查 |
| **P3** | `EmailNotifier` + `website.git_push=true` | 三通道齐备，git push 自动归档 |
