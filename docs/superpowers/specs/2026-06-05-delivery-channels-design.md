# Delivery Channels Design

> **Goal:** Add human-in-the-loop (HITL) review via Telegram + multi-channel delivery (Email, Website), with SQLite state replacing JSON files.

**Architecture:** Pipeline (unchanged) → Notifier abstraction → SQLite state → scheduled ticks (local cron or GH Actions).

**Tech Stack:** Python 3.12, python-telegram-bot (polling + webhook), SQLite (aiosqlite), SMTP (smtplib), Markdown files for website, GitHub Actions YAML.

---

## 1. Context & Goals

Seven-layer pipeline already runs end-to-end (`--dry-run --publish`). Every run is `is_pending=True` because no human ever fills `decisions.json`. This design closes the loop:

1. Three daily ticks (12:00 / 16:00 / 20:00) push review cards to Telegram
2. User taps ✅/❌/⏭ per card on phone
3. 22:00 finalize tick reads decisions → `review()` → `publish()` → Email + Website

Supports **both transports** (A = local polling, B = GH Actions + webhook) via the same Notifier protocol.

---

## 2. New Files

| Path | Responsibility |
|---|---|
| `src/core/types.py` | `DeliveryConfig`, `TickConfig`, `ReviewDecisionDB` (new; replaces `ReviewDecision` file-based) |
| `src/state/db.py` | SQLite schema + async helpers (`aiosqlite`) |
| `src/notifiers/__init__.py` | `Notifier` protocol |
| `src/notifiers/telegram_polling.py` | Long-poll bot (local/VPS) |
| `src/notifiers/telegram_webhook.py` | Webhook bot (serverless / GH Actions) |
| `src/notifiers/email_smtp.py` | SMTP final report |
| `src/notifiers/website.py` | Write `docs/daily/YYYY-MM-DD.md` |
| `src/pipeline/tick.py` | `run_collect_tick()` + `run_finalize_tick()` orchestrators |
| `src/cli.py` | `--tick collect` / `--tick finalize` flags |
| `config/delivery.yaml` | All credentials + schedule config |
| `.github/workflows/daily.yml` | GH Actions cron (Plan B) |
| `launchd/ai-newsday.plist` | macOS launchd (Plan A) |

---

## 3. Data Model (SQLite)

### 3.1 Schema (`src/state/db.py`)

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tick    TEXT NOT NULL,        -- 'collect' | 'finalize'
    ts      TEXT NOT NULL,        -- ISO datetime
    status  TEXT NOT NULL,        -- 'running' | 'done' | 'error'
    notes   TEXT
);

CREATE TABLE IF NOT EXISTS pending_reviews (
    item_id    TEXT PRIMARY KEY,   -- cluster_id or sha256(link)
    run_id     TEXT NOT NULL,
    link       TEXT NOT NULL,
    source     TEXT NOT NULL,
    title_en   TEXT NOT NULL,
    title_zh   TEXT,
    summary_zh TEXT,
    takeaway   TEXT,
    hot_take   TEXT,
    score      INTEGER,
    signals    TEXT,               -- JSON blob
    msg_id     INTEGER,            -- Telegram message_id after send
    status     TEXT DEFAULT 'pending',  -- 'pending'|'keep'|'drop'|'skip'
    decided_at TEXT,
    tick_id    TEXT NOT NULL,      -- which tick created this row
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    link       TEXT NOT NULL,
    source     TEXT NOT NULL,
    action     TEXT NOT NULL,      -- 'keep'|'drop'|'edit'
    run_id     TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_weights (
    source     TEXT PRIMARY KEY,
    weight     REAL NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 3.2 Rationale

- Replaces `decisions.json`, `data/feedback_events.json`, `data/quality_weights.json`
- `pending_reviews.status = 'skip'` = not decided yet, re-shown next tick
- `signals` JSON blob carries `upvotes`, `hn_points`, etc. for display in card
- `feedback_events` and `quality_weights` satisfy ROADMAP §5 item #4 (SQLite persistence)

---

## 4. Notifier Protocol

```python
# src/notifiers/__init__.py
from typing import Protocol, runtime_checkable
from src.core.types import InterpretedItem

@runtime_checkable
class Notifier(Protocol):
    async def send_review_card(self, item: InterpretedItem,
                               signals: dict) -> int | None:
        """Push one review card. Returns platform message_id (for edit/delete).
        Returns None if channel doesn't support interactive review."""
        ...

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        """Send completed daily report. summary = {item_count, must_read_count, date_label}."""
        ...

    async def poll_decisions(self) -> list[tuple[str, str]]:
        """Drain pending button taps since last call.
        Returns list of (item_id, action) where action ∈ {'keep','drop','skip'}.
        Polling notifiers return queued taps; webhook notifiers return [] (decisions
        arrive via HTTP callback and are already written to DB)."""
        ...
```

### 4.1 TelegramPollingNotifier

- Uses `python-telegram-bot` v21 `Application.run_polling()` in a background thread
- `send_review_card`: calls `bot.send_message` with `InlineKeyboardMarkup`
  - 3 buttons: `✅ 留` / `❌ 删` / `⏭ 跳`
  - callback_data = `"{item_id}:{action}"`
- Button tap handler writes `(item_id, action)` to an `asyncio.Queue`
- `poll_decisions()` drains the queue non-blocking
- Card format (Telegram MarkdownV2):
  ```
  *[{label}] {title_zh}*
  _{title_en}_

  📊 {score}分 ｜ 👍 {upvotes} ｜ 🔥 HN {hn_points}
  🔗 {source}

  💬 *一句话*：{summary_zh}
  🛠 *对你*：{takeaway}
  ⚡️ *锐评*：{hot_take}
  ```

### 4.2 TelegramWebhookNotifier

- Same card format, but instead of polling uses `setWebhook`
- Button callback arrives via HTTP POST to webhook URL
- Writes decision directly to SQLite `pending_reviews` (no queue needed)
- `poll_decisions()` returns `[]` — decisions are written asynchronously

### 4.3 EmailNotifier

- Uses `smtplib.SMTP_SSL` with credentials from `config/delivery.yaml`
- `send_review_card()` → raises `NotImplementedError` (email doesn't do HITL)
- `send_final_report()`: sends HTML email (convert Markdown → HTML via `markdown` lib)
- Subject: `AI Daily · {date_label} ({must_read_count} 必读)`

### 4.4 WebsiteNotifier

- `send_review_card()` → no-op (returns None)
- `send_final_report()`: writes `docs/daily/{YYYY-MM-DD}.md`
- Optionally `git add + git commit` to push to GitHub Pages (config flag)

---

## 5. Tick Orchestration

### 5.1 `run_collect_tick(config, db, notifiers)` — runs at 12:00/16:00/20:00

```
1. collect() + enrich_with_hn()
2. dedup() → interpreted_items = interpret()
3. For each item NOT already in pending_reviews for today:
   a. Insert row (status='pending')
   b. For each notifier that supports send_review_card: push card, store msg_id
4. Sleep 0 (decisions arrive async via button taps)
5. Drain poll_decisions() from polling notifiers → UPDATE pending_reviews SET status=action
6. emit 'tick_collect_done'
```

### 5.2 `run_finalize_tick(config, db, notifiers)` — runs at 22:00

```
1. Query all pending_reviews for today
2. Unreviewed (status='pending' or 'skip') → default action='keep'
3. Build decisions dict {link: ReviewDecision(action=...)}
4. review(interpreted_items, daily_take, decisions) → ReviewResult
5. publish(review_result, date_label) → PublishResult
6. For each notifier: send_final_report(markdown, summary)
7. Persist feedback_events + update quality_weights in SQLite
8. emit 'tick_finalize_done'
```

---

## 6. Config

### `config/delivery.yaml`

```yaml
telegram:
  bot_token: ""                    # from BotFather; override via TELEGRAM_BOT_TOKEN env
  chat_id: ""                      # your personal chat; override via TELEGRAM_CHAT_ID env
  mode: "polling"                  # "polling" (local) | "webhook" (serverless)
  webhook_url: ""                  # only if mode=webhook; e.g. https://worker.example.com

email:
  enabled: false
  smtp_host: "smtp.gmail.com"
  smtp_port: 465
  username: ""
  password: ""                     # App Password; override via EMAIL_PASSWORD env
  to: ""

website:
  enabled: true
  output_dir: "docs/daily"
  git_push: false                  # true = auto git add+commit after finalize

schedule:
  collect_ticks: ["12:00", "16:00", "20:00"]
  finalize_tick: "22:00"
  timezone: "Asia/Shanghai"
```

---

## 7. CLI Changes

```bash
# Collect tick (new items → Telegram review cards)
python -m src.cli --tick collect

# Finalize (decisions → review → publish → email + website)
python -m src.cli --tick finalize

# Existing dry-run still works unchanged
python -m src.cli --dry-run --publish
```

---

## 8. Deployment Configs

### 8.1 Plan A — macOS launchd (`launchd/ai-newsday-collect.plist`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" ...>
<plist version="1.0"><dict>
  <key>Label</key><string>ai.newsday.collect</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/.venv/bin/python</string>
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
    <key>TELEGRAM_BOT_TOKEN</key><string>YOUR_TOKEN</string>
    <key>TELEGRAM_CHAT_ID</key><string>YOUR_CHAT_ID</string>
    <key>MODELSCOPE_API_KEY</key><string>YOUR_KEY</string>
  </dict>
</dict></plist>
```

### 8.2 Plan B — GitHub Actions (`.github/workflows/daily.yml`)

```yaml
name: AI Daily

on:
  schedule:
    - cron: "0 4 * * *"    # 12:00 CST = 04:00 UTC
    - cron: "0 8 * * *"    # 16:00 CST
    - cron: "0 12 * * *"   # 20:00 CST
    - cron: "0 14 * * *"   # 22:00 CST (finalize)
  workflow_dispatch:
    inputs:
      tick:
        description: "collect or finalize"
        required: true
        default: "collect"

jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv sync
      - name: Restore SQLite state
        uses: actions/cache@v4
        with:
          path: data/state.db
          key: state-db-${{ github.run_id }}
          restore-keys: state-db-
      - name: Run tick
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          MODELSCOPE_API_KEY: ${{ secrets.MODELSCOPE_API_KEY }}
        run: |
          TICK="${{ github.event.inputs.tick || (contains(github.event.schedule, '14') && 'finalize' || 'collect') }}"
          uv run python -m src.cli --tick $TICK
      - name: Commit daily report if finalize
        if: contains(github.event.schedule, '14') || github.event.inputs.tick == 'finalize'
        run: |
          git config user.email "bot@ai-newsday"
          git config user.name "AI Newsday Bot"
          git add docs/daily/ || true
          git diff --staged --quiet || git commit -m "daily: $(date +%Y-%m-%d)"
          git push
```

**Note on GH Actions state:** SQLite `data/state.db` is cached between runs in the same day using `actions/cache` with day-scoped key. Decisions written by webhook callback (from Telegram) are passed back to GH Actions via a small serverless function (Cloudflare Worker or Vercel) that commits to a `decisions` branch or calls `workflow_dispatch`. This is the one additional complexity vs. Plan A.

---

## 9. Error Handling

| Situation | Handling |
|---|---|
| Telegram send fails | Log + skip card (don't crash tick) |
| No decisions by finalize | All pending → `keep` + "未审" watermark |
| SMTP error | Log + skip email (website still written) |
| Git push fails in website notifier | Log + continue (local file still written) |
| DB locked | Retry 3× with 1s backoff |
| Interpret all fallback | Still finalize (extractive summaries) |

---

## 10. Testing

| Type | What |
|---|---|
| contract | `DeliveryConfig` schema; DB schema migrations; `Notifier` protocol compliance |
| unit | `TelegramPollingNotifier.send_review_card` with mock `bot`; `EmailNotifier` with mock SMTP; `WebsiteNotifier` file write |
| golden | `run_collect_tick` with `FakeNotifier` (captures sent cards); `run_finalize_tick` with pre-seeded decisions |
| integration | Manual: launchd + real Telegram bot end-to-end |

---

## 11. Phase Plan

| Phase | Scope | Deliverable |
|---|---|---|
| **P1** | SQLite schema + `TelegramPollingNotifier` + `run_collect_tick` + `run_finalize_tick` + `WebsiteNotifier` + launchd config | Local end-to-end: tap on phone → report in `docs/daily/` |
| **P2** | `TelegramWebhookNotifier` + GH Actions workflow + Cloudflare Worker stub | Cloud end-to-end without mac running |
| **P3** | `EmailNotifier` + `git_push` for website | Three channels complete |
