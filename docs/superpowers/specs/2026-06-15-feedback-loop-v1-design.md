# 设计 — 反馈闭环 v1(端到端闭环）

> 路径：`docs/superpowers/specs/2026-06-15-feedback-loop-v1-design.md`
> 对应 PRD §4.5（反馈闭环 v1）、§2.1 #6（反馈可回收/可解释/能反哺）。
> 关系：本设计**承接并收尾** `docs/specs/feedback.md` 中明确"延后"的两条 —— **持久化落盘** 与 **接回打分**。纯函数层（`derive_events / aggregate_by_source / compute_quality_weights / feedback`）已实现，本模块只补**持久化 + 接线 + ADR**。

## 1. 目标与范围

**一句话**：把每日审阅的"留/删/改"回收成按源的 `quality_weight`，**持久化到 SQLite**，并**接回第 3 层打分**，让今天的审阅取舍影响明天的排序——形成可见、可解释、幂等的闭环。

**做：**

| 能力 | 说明 |
|---|---|
| 持久化（SSOT=SQLite） | `feedback_events` / `quality_weights` 两张表（`db.py` 已建）真正读写 |
| 接回打分 | `score` 在 `机构影响力` 维度上乘 `quality_weight`（配 ADR） |
| 幂等 | finalize 重跑同 `run_id` 不双计反馈 |
| 可解释 | 复用 `weight_diff`（每源旧→新）；ADR 记录决策 |
| 向后兼容 | 权重缺省 1.0 时打分数值与现状逐字节一致 |

**不做（仍 P1+，与 `feedback.md` 一致）：** 阅读行为/👍👎信号、`reader_relevance` 重算、Qdrant 正反馈向量、explore-exploit 选条策略。

## 2. 关键决策（本次 brainstorm 敲定）

| # | 决策 | 取舍 |
|---|---|---|
| D1 | **端到端闭环**（持久化 + 接线一并做） | 一圈让闭环肉眼可见地闭上，而非分两个 PR |
| D2 | **SQLite 作 SSOT** | PRD 明确 SSOT=SQLite；`db.py` 两表已建；决策数据已在 `pending_reviews`。JSON 账本（`run_dry_feedback`）保留作离线/dry-run 确定性测试 |
| D3 | **`quality_weight` 乘在「机构影响力」维度** | 语义最贴（同为源信誉）；9 个 breakdown key 不变、仍纯加法可解释；默认 1.0 时零行为变化。不选"乘总分"（破坏 `score=各维度之和` 契约）、不选"加第 10 维"（破固定维度集 + snapshot） |
| D4 | **按 `run_id` 幂等 + 保留增量公式** | 复用现有 `compute_quality_weights` 及其 golden 测试（外科手术式）；幂等闸只加在新持久化层。不选"全量重算"（改 §5.4 语义、破 golden）、不选"不管幂等"（cron 重试 → 静默污染权重） |

## 3. 架构与数据流

闭环跑在真实流水线的**两个 tick**上（`src/cli.py::run_tick` → `src/pipeline/tick.py`）：

```
collect tick（每日早）:
  collect→dedup→[读 quality_weights 表]→score(注入权重)→interpret→推卡片→收部分决策

finalize tick（每日晚）:
  collect→dedup→[读 quality_weights 表]→score(注入权重)→interpret
    →读累积决策→review→publish→发报
    →【闭环新增】has_feedback_for_run 守卫
        → derive_events(全量 interpreted_items, decisions)
        → append_feedback_events(INSERT OR IGNORE)
        → feedback(run_events, prior=get_quality_weights(), cfg, ctx)
        → upsert_quality_weights(fres.quality_weights)
```

**时序**：finalize 写权重 → 影响**次日** collect+finalize 的打分；当日两 tick 读同一份（昨日）权重，一致。派生只覆盖**有显式决策**的条目（显式 `drop` 仍产 `drop`，不漏负反馈；无决策=沉默=中性，不产事件）。

## 4. 组件改动（逐文件）

### A. `src/pipeline/score.py`（纯函数加注入）
- `compute_scores(...)`、`score(...)` 新增 `quality_of: dict[str, float] | None = None`（source→权重，缺省 → 全 1.0）。
- `机构影响力 = (dims.get("机构影响力",0) + prio_bonus) * quality_of.get(it.source, 1.0)`。
- 其余维度、key 顺序、`raw=sum(...)`、clamp 不变。**调用方注入权重**（score 同步、DB 异步，score 不碰 DB）。

### B. `src/state/db.py`（新增方法 + schema 约束）
- `feedback_events` 加 `UNIQUE(run_id, link)`（幂等闸）。
- `append_feedback_events(events: list[FeedbackEvent])` — `INSERT OR IGNORE`。
- `get_quality_weights() -> dict[str, float]` — 空表 → `{}`。
- `upsert_quality_weights(weights: dict[str, float])` — `INSERT OR REPLACE` + `updated_at`。
- `has_feedback_for_run(run_id: str) -> bool` — 幂等判断。

### C. `src/pipeline/feedback.py`（零改动）
- 纯函数 `derive_events / aggregate_by_source / compute_quality_weights / feedback` 原样复用，golden 测试不动。

### D. `src/pipeline/tick.py`（接线）
- `run_finalize_tick` 在 publish 之后追加闭环段（守卫 → 派生 → 入账 → 重算 → 写回），发 `feedback_start/weights_computed/feedback_done` runs 事件。
- DB 写失败非致命：记 `feedback_persist_error`，不回滚已发布的报（发布在前）。

### E. `src/cli.py`（`run_tick` 内 `_collect_and_interpret`）
- `score()` 前：`weights = await db.get_quality_weights()`；`score(..., quality_of=weights)`。两 tick 共用此路径，自动生效。

### F. ADR + spec
- 新增 `docs/adr/0002-feedback-quality-weight-into-scoring.md`：记录 D2（JSON→SQLite 取舍）、D3（乘机构影响力）、D4（幂等+增量）。
- 更新 `docs/specs/feedback.md`：把"不接回打分 / 不落盘"由"延后"改为"本模块实现"，补 SQLite 持久化 + 幂等 + 打分接线契约。

## 5. 边界 / 错误处理（都不致命）

| 情况 | 处理 |
|---|---|
| 权重表空（冷启动） | `get_quality_weights()` → `{}`，全源 1.0，打分不变 |
| finalize 重跑同 run_id | `has_feedback_for_run` 命中 → 跳过派生+重算（幂等） |
| 某源样本 < `min_events` | 复用现有逻辑，不动权重 |
| 无事件 / `[SILENT]` 日 | `feedback` 返回 `is_silent`，权重透传，不写表 |
| DB 写失败 | 记 `feedback_persist_error`，不阻断已发布的报 |
| 真实环路只有 keep/drop | `get_decisions_dict` 现仅存 keep/drop；`edit` 暂不出现，`edit_factor` 保留待用 |
| 无决策条目（未审） | `derive_events` 不为其派生事件（沉默=中性，不升不降权）；显式 `drop` 仍回收为负反馈 |

## 6. 测试（目标驱动）

| 类型 | 测什么 |
|---|---|
| contract | 新 db 方法（空表→`{}`、`INSERT OR IGNORE` 幂等、写回往返）；`score(quality_of=...)` 签名/形状 |
| golden | 现有 feedback golden 保持全绿；`quality_of={}` 时 `score` 输出与现状**逐字节一致**；权重 1.5/0.5 时 `机构影响力` 按比例变、9 key 不变 |
| integration | FakeNotifier + 临时 sqlite：finalize → 断言两表落账；同 run_id 重跑 → 断言不双计；权重落账后下一次 `score` 读到并改变 `机构影响力` |

**验收线：** ① `quality_of={}` 时全链行为零变化（旧测试全绿）；② finalize 后权重落 SQLite 且 `weight_diff` 可解释；③ 同 run_id 重跑幂等；④ integration 断言次日打分被权重影响。

## 7. 交付物

- 代码：`score.py` / `db.py` / `tick.py` / `cli.py` 改动 + 闭环 integration 测试。
- 文档：`docs/adr/0002-*.md` 新增；`docs/specs/feedback.md` 更新。
- PR：单模块小 PR，开 issue（遵循 git-pr-conventions：有意义的分支名 + issue-per-PR，英文），描述写明实现哪条 spec / 新增哪些测试 / dry-run 产物在哪。
