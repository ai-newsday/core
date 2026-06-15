# ADR 0002 — 反馈 quality_weight 接回打分

- 状态: Accepted
- 日期: 2026-06-15
- 关联: PRD §4.3 / §4.5；`docs/specs/feedback.md`；设计 `docs/superpowers/specs/2026-06-15-feedback-loop-v1-design.md`

## 背景

反馈层的纯函数已能从审阅"留/删/改"算出每源 `quality_weight`，但此前明确"不接回打分、不落盘"。本 ADR 记录把闭环真正闭上的三个决策。

## 决策

1. **SSOT = SQLite**。复用 `db.py` 已建的 `feedback_events` / `quality_weights` 表持久化；JSON 账本（`run_dry_feedback`）保留作离线/dry-run 确定性测试。理由：PRD 明确 SSOT 为 SQLite，决策数据已在 `pending_reviews`。
2. **`quality_weight` 乘在 `机构影响力` 维度**：`机构影响力 = (基础 + priority_bonus) * quality_weight`。理由：语义同为源信誉；保持 9 个 breakdown key 不变与纯加法可解释；默认 1.0 时数值零变化。否决"乘总分"（破坏 score=各维度之和）与"加第 10 维"（破坏固定维度集 + snapshot）。
3. **增量公式 + run_id 幂等**：保留现有 `compute_quality_weights` 增量漂移模型及其 golden 测试；幂等闸 `UNIQUE(run_id, link)` + `has_feedback_for_run` 只加在持久化边界。否决"全量重算"（改语义、破 golden）与"不管幂等"（cron 重试污染权重）。
4. **无决策=沉默=中性**：`derive_events` 只为有显式 keep/drop/edit 决策的条目派生事件；未审条目不产事件、不改权重。理由：真实 Telegram 审阅是部分覆盖（120s 轮询窗口），若把"没审"记作 keep，则源权重会仅因沉默而爬向 `max_weight` 上限——"忽略某源"会被误读成"主动保留某源"。显式 drop 仍回收为负反馈。

## 影响

- 权重缺省 1.0 ⇒ 现有打分 golden/snapshot 全绿（向后兼容）。
- finalize tick 在发布之后写权重；影响**次日**打分。写失败非致命（`feedback_persist_error`，不回滚已发布的报）。
- 阅读行为/👍👎信号、`reader_relevance`、Qdrant 向量、explore 选条仍为 P1+，本 ADR 不涉及。
- 注意迁移：`feedback_events` 的 `UNIQUE(run_id, link)` 通过 `CREATE TABLE IF NOT EXISTS` 建立，不会回溯应用到已存在的旧表；这两张表此前从未写入，仅当本地有陈旧 `data/state.db` 时需删除该文件或重建表。
</content>
