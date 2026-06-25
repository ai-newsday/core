# 放宽发卡池 — 设计

日期: 2026-06-25 · 对应 KANBAN §3 P0 · 相关 memory: [[review-card-pool-equals-quota]]

## 问题

低优先级 / 零信号源的重要发布（如 Krea-2，来自 comfy priority-3 writeup）打分上不来，
**在发卡前就被 quota 砍掉** → 用户在 Telegram 根本看不到、无从 keep。

查实链路（[cli.py](../../../src/cli.py) `_collect_and_interpret`）后发现**有两道闸**，交接里只识别了第一道：

```
collect → dedup → score ──┬─ selected_items (quota→top-11) → interpret → 发卡
                          └─ all_scored (全量, 被丢弃)
```

- **闸 1（发卡闸，根因）**: `interpret(sres.selected_items)` 只解读 quota 选中的 top-11
  → 只有这 11 条发卡。低分条目不发卡 → 用户看不到。
- **闸 2（发布闸）**: finalize 链路（[tick.py](../../../src/pipeline/tick.py) `select_report_items`
  → [publish.py](../../../src/pipeline/publish.py) `build_report`）最终报告 = 人工显式 keep/edit
  **且** `score >= min_display_score(60)` **且** `relevant`。`total_limit` 在 publish **没再 apply**。
  → 即便放宽闸 1 让低分条目发了卡、用户 keep，60 地板照样把它砍掉。

**只放宽发卡池不够**，必须两道闸联动。

## 目标 / 验收标准

1. 发卡阶段解读并推送一个**更宽的候选池**（不再 quota→11），让 score 上不来的相关首发也能发卡、被 keep。
2. 用户显式 keep 的低分条目能进当日刊物（不被 60 地板默默吞）。
3. 最终刊物仍**有界且分类均衡**：per-genre 配额 + 总量上限，在人 keep 之后施加。
4. interpret（每条一次 Sonnet）成本有**硬上界**。
5. 全程纯函数 / config 旋钮可调，dry-run 诊断不受影响。

## 设计

### 数据流（改后）

```
collect → dedup → score → all_scored
        → 发卡池 = all_scored[:card_pool_limit]  (按 score top-N, 默认 25)
        → interpret(发卡池) → 发卡 (relevant 的)

finalize: 人 keep/edit (select_report_items)
        → keep 地板过滤 (keep_min_display_score, 默认 40)
        → per-genre quota + total_limit (对 kept 集合重施)
        → publish
```

### 三处改动

**1. 发卡池放宽（闸 1）** — `score.py` `score()`
重定义 `ScoreResult.selected_items` ＝ 「发卡候选」＝ `all_scored[:card_pool_limit]`
（不再是 quota→11）。`all_scored` 已按 `(score desc, published_at asc, link asc)` 排序，
直接切片即得 top-N。新增 `card_pool_limit`（默认 25）到 `ScoringConfig`，移除 `quota`/`total_limit`。
**`cli.py` 的 `interpret(sres.selected_items)` 一行不动**——改的是 selected_items 的含义。
score 阶段不再砍 per-genre 配额。

**2. 配额移到 publish（闸 2 的组成控制）** — `publish.py` `build_report`
人 keep 后，对 kept 的 `ReviewedItem` 集合施加 per-genre 配额 + total_limit。
复用 score.py 既有纯函数 `apply_quota` 的逻辑（按 `(score desc, published_at, link)` 取每类 top-N，
再总量截）。`ReviewedItem` 带 `.score` / `.genre`，可直接复用。

**3. keep 地板下调（闸 2 的质量底）** — `publish.yaml`
`min_display_score` 60 → 40，语义改为「人工 keep 条目的质量底」。
确认门已保证 publish 阶段全是人工 keep 的条目，故此地板**只对 keep 条目生效**；
60 太高会吞掉用户故意 keep 的低分首发。

### 配置边界（选定方案 A）

`quota` + `total_limit` 从 `scoring.yaml` **迁到 `publish.yaml`**——它们现在塑造的是
「刊物组成」而非「打分」。
- `PublishConfig` 新增 `quota` / `total_limit`。
- `ScoringConfig` 移除 `quota` / `total_limit`，新增 `card_pool_limit`。
- score.py 的 `selected_items` 退出**线上链路**（不再喂 interpret）。dry-run 诊断
  `03_scored.jsonl` 改 dump `all_scored`（全量打分视图），`selected_items` 字段保留向后兼容
  但置为 `all_scored[:card_pool_limit]`（= 发卡池），语义统一为「会发卡的候选」。
  `apply_quota` 纯函数从 score.py **移到 publish.py**（配额逻辑随配额配置走）。

### 初值（均为 config 旋钮，事后可调）

| 旋钮 | 值 | 位置 |
|---|---|---|
| `card_pool_limit` | 25 | `scoring.yaml` |
| `min_display_score`（keep 地板） | 40 | `publish.yaml` |
| `quota` | 沿用现值 `{paper:3,model:3,announcement:3,writeup:2,news:1}` | `publish.yaml` |
| `total_limit` | 沿用现值 `11` | `publish.yaml` |

## 已知天花板（ponytail）

发卡池 top-N 按**纯 score** 排，不分 genre。极端情形：某天 25 条全是 papers，
rank 30 的重要 announcement 不发卡 → 用户看不到 → 无从 keep → publish 的 announcement
配额形同虚设。`card_pool_limit=25`（>2× 旧的 11）已大幅降低该风险。
**升级路径**：若观察到 genre 饿死，把发卡池从「top-N」改为「widened per-genre quota」。先不做（YAGNI）。

## 不做（YAGNI）

- 不在发卡阶段引入第二套 per-genre 配额。
- 不改 review / webhook 决策协议。
- 不动 dedup / score 打分公式本身。

## 测试要点（TDD）

- 发卡池: `all_scored` 长度 > card_pool_limit 时只解读前 N；< N 时全解读。
- publish 配额: kept 集合某 genre 超配额 → 只留该类 top-N（按 score）；总量超 total_limit → 截断。
- keep 地板: score 在 [40,60) 的 kept 条目**进**报告；< 40 的 kept 条目被过滤。
- 配置迁移: `PublishConfig` 读到 quota/total_limit；`ScoringConfig` 读到 card_pool_limit。
