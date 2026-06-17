# Spec — 打分排序层 (Score / Ranking)

> 放置路径：`docs/specs/score.md`。这是七层流水线的第 3 层，MVP 第三个要实现的模块。
> 对应 PRD §3.2（NewsItem 增量 score/score_breakdown/is_explore）、§4.2（类型配额）、§4.3（打分模型 v2 维度）、§5.5（score_breakdown 维度键）、§2.1（验收 #4 配额生效）。
> 上游：第 2 层去重 (`docs/specs/dedup.md`) 产出的 `DedupResult.deduped_items: list[NewsItem]`（去重主条目，带 `cluster_id`）。下游：第 4 层解读（消费本层入选的高分主条目）。

## 1. 目的

把上游"去重后未排序"的 `NewsItem` 列表，按 PRD §4.3 多维规则**打 0-100 分**（每条带可解释 `score_breakdown`），再按 PRD §4.2 **类型配额**筛选出每期最终入选条目（≈7 条）。

直接服务的痛点：信息过载下"选得准 > 写得好"（PRD §1.3），日报质量上限由**排序**决定。本层成败标准是 **PRD #4：配额生效**（golden 断言：输出条目数与类型 100% 符合 config 配额）。

## 2. 范围 / 非目标

- **做**：多维规则打分（确定性、全读 `config/scoring.yaml`）、类型配额筛选、产出 `ScoreResult`、写 `runs` 事件、支持 `--dry-run`。
- **不做（本圈明确延后，对齐 PRD line58「打分 v1 先不含个性化」）**：
  - **读者相关度 / 画像向量**（`reader_relevance`）：MVP 权重为 0（PRD §2.1 line89 明确 out-of-scope）；breakdown 留 `读者相关度: 0` 占位，反馈闭环圈再启用。
  - **explore 配额**（多样性名额）：与读者相关度同属"打分 v2 / P1"（PRD line65）；本圈 `is_explore` 恒 `False`，留字段占位。
  - **可见指标**（HF upvotes / GitHub stars）：当前 `RawItem` 不携带这些字段；本圈 breakdown 留 `可见指标: 0` 占位，**不横跨改采集层**（CLAUDE.md「一次只做一层」）。后续圈给 `RawItem` 加 metrics 字段时再启用。
  - **LLM 打分**：本圈纯规则启发式，无 LLM / 无网络（解读层才引入 LLM）。
  - 翻译 / 摘要 / 解读 / 锐评 / 证据链（第 4 层）。

## 3. 接口契约

```python
def score(items: list[NewsItem], config: ScoringConfig, ctx: RunContext) -> ScoreResult: ...
```

- **输入**：
  - `items: list[NewsItem]` —— 上游去重产物（`DedupResult.deduped_items`，每个 cluster 的 primary）。
  - `config: ScoringConfig`（见 §6，全部维度分/权重/配额读 `config/scoring.yaml`，不写死）。
  - `ctx: RunContext` —— 复用上游的 `run_id` / `now`（注入，确定性，用于时效计算）/ `logger`。
- **纯函数核心（无 IO / LLM / 网络，本层可测核心）**：
  - `compute_scores(items, priority_of, config, ctx) -> list[ScoredItem]` —— 打分。
  - `apply_quota(scored, config) -> tuple[list[ScoredItem], dict[str, QuotaLine]]` —— 配额筛选。
- **唯一外部读 = registry 优先级 map**：`score()` orchestrator 读 `config.sources_registry_path` 取 `{source_name: priority}`（复用 dedup 的 `load_source_priorities`，与 dedup 相同的 **registry 映射注入** 模式），再注入纯函数。RawItem 不改。

> **IO 隔离（CLAUDE.md 架构约束）**：本层无网络/无 LLM；打分与配额是纯函数（输入 items + priority map → scored/selected），全程离线可测、确定性。

## 4. 数据契约

```python
class ScoredItem(NewsItem):              # NewsItem 的下游演进; 本圈加打分字段
    score: int                           # 0-100, clamp 后整数
    score_breakdown: dict[str, float]    # PRD §5.5 维度键; 可解释、可调权重
    is_explore: bool = False             # 本圈恒 False(explore 延后 v2), 占位

class QuotaLine:
    genre: str                           # 体裁
    available: int                       # 该 genre 打分后可选条目数
    quota: int                           # config 配额上限
    selected: int                        # 实际入选 == min(quota, available)

class ScoreResult:
    selected_items: list[ScoredItem]     # 配额筛选后的最终入选(下游 L4 消费), 按 score 降序
    all_scored: list[ScoredItem]         # 全部打分条目(含未入选), 供复盘/反馈圈, 按 score 降序
    quota_report: dict[str, QuotaLine]   # key = genre
    input_count: int                     # 入参 items 数
    selected_count: int                  # == len(selected_items)
    is_silent: bool                      # input_count == 0
```

> `score_breakdown` 的键恒为 PRD §5.5 的 9 个维度（缺失维度置 0 占位，不增删键），保证对下游 L4 渲染器/反馈圈契约稳定。

## 5. 算法（确定性）

### 5.1 单条打分（`compute_scores`）

`score_breakdown` 恒含以下 9 个维度键（PRD §5.5），逐项计算后求和、clamp：

| 维度 | v1 计算来源 | 备注 |
|---|---|---|
| `机构影响力` | `publisher_authority[publisher]` + `priority_bonus[priority]` | 权威性由 publisher 决定（b1 单标量），priority 折进此维度，不另立 breakdown 键 |
| `一手性` | `genre_value[genre].一手性` | paper/announcement/model 高，writeup 中，news 低 |
| `技术价值` | `genre_value[genre].技术价值` | |
| `产业影响` | `genre_value[genre].产业影响` | |
| `扩散潜力` | `genre_value[genre].扩散潜力` | |
| `可见指标` | `0` | 本圈跳过（RawItem 无 metrics），占位 |
| `时效` | `recency_band(published_at, ctx.now)` | 见 §5.2 |
| `惩罚` | `same_source_penalty`（≤0） | 见 §5.3 |
| `读者相关度` | `0` | MVP 权重 0，占位 |

`score = clamp(round(sum(breakdown.values())), 0, 100)`。

### 5.2 时效分（`recency_band`）

设 `age_h = (ctx.now - published_at) 小时数`（用注入的 `ctx.now`，不依赖真实当前时间）：

- `age_h <= recency.fresh_hours` → `recency.fresh_bonus`
- `recency.fresh_hours < age_h <= recency.mid_hours` → `recency.mid_bonus`
- `recency.mid_hours < age_h <= recency.stale_hours` → `0`
- `age_h > recency.stale_hours` → `recency.stale_penalty`（负）

### 5.3 同源惩罚（`惩罚`）

同一 `source` 出现多条时，按 **`(published_at 升序, popularity 降序, link 升序)`** 排序：**第一条惩罚 0，其余每条各得 `penalty.same_source`（负）**。

- `popularity = sum(weight * value)`，复用 `popularity_weights` 信号集（`upvotes / likes / hn_points / num_comments`）的**原始信号**，不开 `sqrt`/`cap`——只作排序键，**不是 score**。
- **不依赖打分输出**——排序键全部来自 `item.published_at` 与 `item.signals`，均为输入侧字段，避免"惩罚→分数→排序"循环依赖。
- 当 `published_at` 撞车（如 HF 每日精选 `submittedOnDailyAt` 全部=今日 00:00 UTC）时，由 popularity 决定谁免罚；popularity 也并列则 `link` 字母序兜底（确定性）。无 `popularity_weights` 配置或无相关 signals 时，popularity 全为 0，行为退化为旧的 `(published_at, link)`。

### 5.4 体裁配额筛选（`apply_quota`，PRD §4.2）

1. 按 `genre` 分组；组内按 `score` 降序，**同分用 `published_at` 升序再用 `link` 升序兜底**（确定性 tie-break）。
2. 每组取前 `quota[genre]` 条（`quota` 缺该 genre 视为 0）：`selected[genre] = 该组前 min(quota[genre], available[genre]) 条`。
3. `selected_items` = 各 genre 入选并集，按 `score` 降序（同分同 §5.4.1 tie-break）排列。
4. **严格按 genre，不跨 genre 补位**（v1 保持简单；余量跨 genre fill 留 v2）。`total_limit` 作为总上限的健全性校验：`sum(quota.values()) <= total_limit` 由 config 保证；若入选总数超过 `total_limit`（理论上不会，因各 quota 之和受限），截断最低分。
5. `quota_report[genre] = QuotaLine(genre, available, quota, selected)`，覆盖出现过的全部 genre。

## 6. 配置：`config/scoring.yaml`

```yaml
# genre → 4 维内容价值(一手性/技术价值/产业影响/扩散潜力, PRD §5.5 维度键). 全部可调, 不写死.
genre_value:
  paper:        {一手性: 20, 技术价值: 16, 产业影响: 8,  扩散潜力: 7}
  model:        {一手性: 18, 技术价值: 14, 产业影响: 10, 扩散潜力: 9}
  announcement: {一手性: 20, 技术价值: 10, 产业影响: 12, 扩散潜力: 9}
  writeup:      {一手性: 12, 技术价值: 12, 产业影响: 8,  扩散潜力: 9}
  news:         {一手性: 8,  技术价值: 6,  产业影响: 12, 扩散潜力: 11}

# publisher → 机构影响力(b1 单标量). priority_bonus 仍叠加其上.
publisher_authority: {lab: 18, company: 14, individual: 8, media: 12}

# 优先级调节: registry 中 source.priority(1=最高) → 折进"机构影响力"维度
priority_bonus: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4}
priority_bonus_default: 0          # 未列出的 priority 取此值

recency:                          # 时效分(§5.2)
  fresh_hours: 24
  fresh_bonus: 10
  mid_hours: 48
  mid_bonus: 4
  stale_hours: 72
  stale_penalty: -10

penalty:
  same_source: -5                 # 同源第2+条各扣(§5.3)

# genre 配额(PRD §4.2). 各 quota 之和应 <= total_limit.
quota: {paper: 2, announcement: 2, writeup: 2, model: 1, news: 1}
total_limit: 8                    # 各 quota 之和(=8) <= total_limit(硬上限); PRD「≈7 条」

sources_registry_path: "config/sources.yaml"   # 取 source.priority map
```

对应 `ScoringConfig`（dataclass，默认值与上表一致）字段：`genre_value: dict[str, dict[str, float]]`、`publisher_authority: dict[str, float]`、`priority_bonus: dict[int, int]`、`priority_bonus_default: int`、`recency`（拍平为 `fresh_hours/fresh_bonus/mid_hours/mid_bonus/stale_hours/stale_penalty`）、`same_source_penalty: float`、`quota: dict[str, int]`、`total_limit: int`、`sources_registry_path: str`。

## 7. 错误与回退（非致命，继承 CLAUDE.md/PRD §3.4）

| 情况 | 处理 |
|---|---|
| 入参 `items == []`（上游 `is_silent`） | 返回空 `ScoreResult`（selected/all_scored 皆空，`is_silent=True`），不抛异常 |
| 缺 `priority`（registry 无此源或注册表缺失） | 取 `priority_bonus_default`（同 dedup 的默认 priority 思路） |
| 某 `genre` 0 条 | 该 genre `selected=0`，不编造（PRD「宁可少写不可编造」） |
| 某 `genre` 不在 `genre_value` / 某 `publisher` 不在 `publisher_authority` | 该维度取 0（enum 封闭，正常不发生；防御性） |
| `--dry-run` | 纯函数内存完成；产出 `ScoreResult` JSON；链路 `collect() → dedup() → score()` |

## 8. 不变量（golden 测试必须断言）

1. **配额生效**（PRD #4）：每类型 `selected == min(quota[type], available[type])`；`selected_count == sum(selected per type)`；`selected_count <= total_limit`。
2. `score ∈ [0, 100]`（clamp 生效，整数）。
3. 同类型内入选**恒为分数最高的若干条**（不会出现低分入选而同类型更高分落选）。
4. **确定性**：同一输入 + 同一 `ctx.now` ⇒ 同分数 / 同入选 / 同顺序（含同分 tie-break）。
5. `is_explore == False` 全员（explore 延后 v2）。
6. `score_breakdown` 恒含 PRD §5.5 的 9 个维度键；`可见指标 == 0` 且 `读者相关度 == 0`（MVP 占位）。
7. `score == clamp(round(sum(score_breakdown.values())), 0, 100)`（可解释：分数 = 各维度之和）。
8. 入参 `[]` → 空 `ScoreResult`，`is_silent=True`，不抛异常。
9. 每个 `ScoredItem` 继承全部 `NewsItem`/`RawItem` 不变量（标题/链接/源/类型/时区非空、`cluster_id` 非空）。

## 9. golden 用例（fixtures 驱动，≥6）

> 测试用**冻结的 NewsItem fixtures**（含 genre / publisher / source / published_at），注入固定 `ctx.now` 与固定 priority map，使打分与入选确定、可断言，不依赖网络与真实时间。

1. **类型配额超额裁剪**：某类型条目数 > quota → 恰取前 quota 条（分数最高），其余落选；`selected[type] == quota[type]`（不变量 1、3）。
2. **配额未满全留**：某类型条目数 < quota → 全部入选，不编造补位；`selected[type] == available[type]`（不变量 1）。
3. **时效打分**：fresh / mid / stale / 超 stale 四条同类型同源外条件 → `时效` 维度分分别为 fresh_bonus/mid_bonus/0/stale_penalty（§5.2）。
4. **同源惩罚**：同一 source 三条 → 按 published_at 升序，最早 `惩罚==0`，其余各 `==same_source`（§5.3，不变量 4 确定性）。
5. **空输入**：`items == []` → 空 `ScoreResult`，`is_silent=True`，不抛（不变量 8）。
6. **确定性 + clamp + breakdown 求和**：极端高/低维度配置 → 分数 clamp 到 [0,100]；断言 `score == clamp(round(sum(breakdown.values())),0,100)`（不变量 2、7）；重复调用结果一致（不变量 4）。

## 10. 测试要求

- **contract**：`ScoringConfig` 加载（`load_scoring_config`）；`ScoredItem` schema 校验（score/breakdown/is_explore）；`QuotaLine`/`ScoreResult` 结构。
- **golden**：用冻结 fixtures 驱动 §9 的 6 个用例，断言 §8 不变量。
- 时间相关一律用注入的 `ctx.now`（时效计算据此），**不依赖真实当前时间**。
- 纯函数 `compute_scores()` / `apply_quota()` 全程离线可测，无网络、无 LLM。

## 11. 可观察

- 每条打分后 emit `item_scored{link, genre, score}`（量大可仅 debug 级或聚合，复用 dedup 的 `emit`）。
- 配额应用后每 genre emit `quota_applied{genre, available, quota, selected}`。
- 每条入选 emit `item_selected{link, genre, score}`（PRD §6.2 埋点）。
- `score()` 结束 emit `score_done{input_count, selected_count, silent}`，写入 `runs`。

## 12. 验收（对齐 PRD §2.1）

- **#4 打分可解释**：每条带 `score_breakdown`（9 维度键，分数 = 各维度之和）；**配额生效**（golden 断言：输出条目数与类型 100% 符合 config 配额）。
- **#1 端到端**：`collect() → dedup() → score()` 可串联，`--dry-run` 产出 `ScoreResult` JSON，无人工干预。
- **#8 静默正确**：上游静默时本层返回空结果（`is_silent=True`），不产空数据、不抛异常。
- **#9 可观察**：`score_done` 等事件写入 `runs`，可复盘"今天为什么选这几条"。
