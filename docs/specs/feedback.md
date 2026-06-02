# Spec — 反馈层 (Feedback)

> 路径：`docs/specs/feedback.md`。七层流水线第 7 层，MVP 收尾模块（闭环）。
> 上游：第 4 层解读产出的 `InterpretedItem`（进审阅前的全量条目）+ 第 5 层审阅的人工决策 `ReviewDecision`（按 link 索引）。下游：未来反哺第 3 层打分（本圈**不接线**，只算 + 落账）。
> 对应 PRD 条款：

| PRD | 说的事 | 本层怎么落 |
|---|---|---|
| §4.5 | 反馈闭环 v1：三类信号（① review_action 留/删/改 最强隐式、② 阅读行为 open/dwell/forward、③ 显式 👍👎） | 本圈**只收信号①** review_action；②③ 延后 P1 |
| §4.5 | 应用：周期性重算 `source.quality_weight` / `reader_relevance` / explore-exploit 选条 | 本圈只算 `quality_weight`（按源信誉）并落账；`reader_relevance`、选条策略延后 P1 |
| §4.5 | 存储：feedback 表 + 正反馈向量进 Qdrant | 本圈用 **JSON 事件账本**（追加式）替代 SQLite/Qdrant；真正落库延后 P1 |
| §4.3 | `reader_relevance` 冷启动权重为 0 | 本圈不产 `reader_relevance`，与冷启动一致（不动它即为 0/不参与） |
| §2.1 #6 | 反馈可回收、可解释、能反哺 | 本圈产 `quality_weights` + `weight_diff`（旧→新可解释），但**不接回打分**，接线是显式的未来改动 |

## 1. 目的

一句话：**把每次运行里"人工对每条做了什么"（留/删/改）回收成按源聚合的信誉信号，增量更新每个源的 `quality_weight`，落成可解释的账本。**

反馈层做三件事，且分开：先从**进审阅前**的全量条目 + 审阅决策**派生事件**（每条一个 `FeedbackEvent`，带 source 与 action）；再**按源聚合**成留/删/改计数；最后用增量公式把上一轮的 `quality_weights` **更新**成新的一轮，并产出旧→新差异。

本圈**只做核心逻辑**（派生 + 聚合 + 算权重 + 落账本），**不接回打分**——`quality_weight` 真正进入第 3 层评分是一处显式的未来改动（届时改 `scoring.py` + 写 ADR），本层只负责把权重算对、存对。

## 2. 范围 / 非目标

**做：**

| 能力 | 说明 |
|---|---|
| 派生事件 | 从进审阅前 `InterpretedItem` + `ReviewDecision`，每条产一个 `FeedbackEvent`（link/source/action/run_id/ts） |
| 按源聚合 | 把事件按 `source` 汇成 `SourceFeedbackStats`（keep/edit/drop/total） |
| 增量算权重 | 用上一轮 `quality_weights` + 本轮聚合，按公式更新成新权重，夹在 [min,max] |
| 可解释差异 | 产 `weight_diff`：每个源 `(旧值, 新值)`，供审计/调参 |
| 样本不足保护 | 某源事件数 < `min_events` 时不动其权重（避免单条噪声抖动） |
| 空输入静默 | 没有事件 → `is_silent=True`，权重原样返回 |
| 产物 + 留痕 | 产 `FeedbackResult`，写 `runs` 事件，天然 `--dry-run`（只算 + 打印，不落盘） |

**不做（这一圈明确延后）：**

| 不做 | 为什么 / 归属 |
|---|---|
| 把 `quality_weight` 接回第 3 层打分 | 改打分行为是显式决策，需配 ADR + 改 `scoring.py`；本层只算不接，避免"偷偷改打分" |
| 阅读行为信号（open/dwell/forward） | 依赖前端埋点/渠道回传，无数据源，P1 |
| 显式 👍👎 信号 | 同上，需交互入口，P1 |
| `reader_relevance` 重算 | 依赖②③信号与读者画像，冷启动为 0，P1 |
| 正反馈向量进 Qdrant | 依赖向量库写入，本圈纯核心 + JSON 账本，P1 |
| SQLite `feedback` 表落库 | 用 JSON 事件账本替代；真正 SSOT 落库 P1 |
| explore-exploit 选条策略 | 反哺选条是打分/调度的事，P1 |
| 真正写磁盘副作用 | 本圈 `--dry-run` 只打印 `quality_weights`/`weight_diff`，不落盘 |

## 3. 接口契约

```python
def feedback(events: list[FeedbackEvent], prior_weights: dict[str, float],
             config: FeedbackConfig, ctx: RunContext) -> FeedbackResult: ...
```

**输入：**

| 参数 | 是什么 |
|---|---|
| `events` | 本轮要计入的反馈事件（本次运行派生的 + 账本里历史的，已合并） |
| `prior_weights` | 上一轮的 `quality_weights`（按 source → float）；缺源视为 `baseline_weight` |
| `config` | 账本路径 / 基准权重 / 上下夹界 / 步长 / edit 系数 / 样本下限，读 `config/feedback.yaml`，不写死 |
| `ctx` | 复用上游 `run_id` / `now` / `logger` |

辅助纯函数：

```python
def derive_events(items: list[InterpretedItem],
                  decisions: dict[str, ReviewDecision],
                  run_id: str, now: datetime) -> list[FeedbackEvent]: ...
def aggregate_by_source(events: list[FeedbackEvent]) -> list[SourceFeedbackStats]: ...
def compute_quality_weights(stats: list[SourceFeedbackStats],
                            prior_weights: dict[str, float],
                            config: FeedbackConfig
                            ) -> tuple[dict[str, float], dict[str, tuple[float, float]]]: ...
```

**纪律（CLAUDE.md 架构约束）：**

- **不调 LLM、不打网络、不抓取、不改打分。** 本圈唯一产物是内存里的权重表 + 账本数据。
- `feedback()` 本体与 `derive_events` / `aggregate_by_source` / `compute_quality_weights` 全是**纯函数**，注入冻结输入就能离线确定性测。
- 公式系数（步长、edit 系数、夹界、样本下限）全读 `config`，不硬编码散落。
- `ts` 由参数 `now` 注入，**层内不取 now**（确定性）。

## 4. 数据契约

```python
class FeedbackEvent(BaseModel):
    link: str                           # 条目唯一标识(同上游)
    source: str                         # 源名(聚合键)
    action: Literal["keep", "drop", "edit"]   # 审阅动作(最强隐式信号)
    run_id: str                         # 哪次运行产生
    ts: datetime                        # 事件时间(由 now 注入)

class SourceFeedbackStats(BaseModel):
    source: str
    keep: int                           # 该源被"留"的次数
    edit: int                           # 该源被"改"的次数
    drop: int                           # 该源被"删"的次数
    total: int                          # keep + edit + drop

class FeedbackConfig:                    # dataclass
    events_path: str = "data/feedback_events.json"   # JSON 事件账本路径
    weights_path: str = "data/quality_weights.json"  # 权重落账路径
    baseline_weight: float = 1.0         # 未知源/冷启动基准
    min_weight: float = 0.5              # 权重下夹界
    max_weight: float = 1.5              # 权重上夹界
    step: float = 0.2                    # 每轮调整步长
    edit_factor: float = 0.5             # edit 记作"半个正向"(改了但留下)
    min_events: int = 1                  # 样本下限, 不足不动权重

class FeedbackResult:                    # dataclass
    source_stats: list[SourceFeedbackStats]          # 按源聚合(source 字母序)
    quality_weights: dict[str, float]                # 更新后的权重表
    weight_diff: dict[str, tuple[float, float]]      # source → (旧, 新)
    event_count: int                                 # 计入的事件总数
    source_count: int                                # 涉及的源数
    is_silent: bool                                  # 无事件 → True
```

**不变式：**

| 等式 | 含义 |
|---|---|
| `stats.total == keep + edit + drop` | 计数自洽 |
| `sum(s.total for s in source_stats) == event_count` | 聚合不漏不重 |
| `min_weight <= w <= max_weight ∀ w ∈ quality_weights.values()` | 权重恒在夹界内 |
| `set(weight_diff) ⊆ set(quality_weights) ∪ set(prior_weights)` | 差异只覆盖出现过的源 |
| `total < min_events ⇒ new == prior.get(source, baseline)` | 样本不足不动权重 |

## 5. 算法（确定性 / 无 IO）

### 5.1 空输入直接返回

`events == []` → 返回 `FeedbackResult`，`source_stats=[]`、`quality_weights = prior_weights`（原样透传）、`weight_diff={}`、`event_count=0`、`source_count=0`、`is_silent=True`，不抛。

### 5.2 派生事件（`derive_events(items, decisions, run_id, now) -> list[FeedbackEvent]`）

| 步骤 | 做什么 |
|---|---|
| 遍历来源 | 遍历**进审阅前**的全量 `InterpretedItem`（这样被删的条目也能产生 `drop` 事件——只看保留下来的会漏掉负反馈） |
| 取动作 | `action = decisions[item.link].action` 若该 link 有决策；否则缺省 `"keep"`（无决策=默认留，对齐审阅层语义） |
| 带源 | `source = item.source`（聚合键） |
| 装事件 | `FeedbackEvent(link, source, action, run_id, ts=now)` |

> `ReviewDecision.action` 取值与审阅层一致：`"keep"` / `"drop"` / `"edit"`（其它如纯 order 调整视作 `"keep"`）。

### 5.3 按源聚合（`aggregate_by_source(events) -> list[SourceFeedbackStats]`）

| 规则 | 说明 |
|---|---|
| 分组键 | `event.source` |
| 计数 | 按 `action` 累加到 keep / edit / drop；`total = keep+edit+drop` |
| 顺序 | 按 `source` **字母序**输出（确定性，与输入顺序无关） |

### 5.4 算权重（`compute_quality_weights(stats, prior_weights, config) -> (weights, diff)`）

对每个源 `s`：

```
old = prior_weights.get(s.source, baseline_weight)
if s.total < min_events:
    new = old                                  # 样本不足, 不动
else:
    kr = s.keep / s.total
    er = s.edit / s.total
    dr = s.drop / s.total
    raw = old + step * (kr + edit_factor * er - dr)
    new = clamp(raw, min_weight, max_weight)
weights[s.source] = new
diff[s.source]    = (old, new)
```

- **方向**：留多 → 升；删多 → 降；改记作"半个正向"（`edit_factor=0.5`，改了但仍采用，弱正信号）。
- **未出现的源**：不在本轮 `stats` 里的源，其 `prior_weights` 原样保留进 `weights`（不丢历史），但不进 `diff`。
- **冷启动**：`prior_weights` 没有的源，从 `baseline_weight` 起步。

### 5.5 编排（`feedback(events, prior_weights, config, ctx) -> FeedbackResult`）

汇总 §5.2–5.4：

```
stats              = aggregate_by_source(events)
weights, diff      = compute_quality_weights(stats, prior_weights, config)
event_count        = len(events)
source_count       = len(stats)
is_silent          = (event_count == 0)
```

> 全程**无随机、无 now()**（`ts` 在 `derive_events` 由参数注入）→ 同输入同输出，可冻结测。

## 6. 配置与加载

### 6.1 `config/feedback.yaml`

```yaml
events_path: "data/feedback_events.json"   # JSON 事件账本
weights_path: "data/quality_weights.json"  # 权重落账
baseline_weight: 1.0                        # 冷启动/未知源基准
min_weight: 0.5                             # 权重下夹界
max_weight: 1.5                             # 权重上夹界
step: 0.2                                    # 每轮步长
edit_factor: 0.5                            # edit 记作半个正向
min_events: 1                               # 样本下限, 不足不动
```

`FeedbackConfig`（dataclass，默认值同上）。加载器 `load_feedback_config(path)` 与 `load_publish_config` 同风格（缺文件 → 默认；`.get` per field）。

### 6.2 账本加载器（纯读，缺文件回空）

```python
def load_feedback_events(path: str) -> list[FeedbackEvent]: ...    # 缺文件 → []
def load_quality_weights(path: str) -> dict[str, float]: ...       # 缺文件 → {}
```

- `load_feedback_events`：读 JSON 数组，每元素过 `FeedbackEvent` 校验（非法 action 即抛 `ValidationError`，对齐 `load_review_decisions` 风格）；缺文件 → `[]`。
- `load_quality_weights`：读 JSON 对象 `{source: float}`；缺文件 → `{}`。
- 二者均**只读不写**。本圈不落盘（写账本是 P1 / 非 dry-run 的事）。

### 6.3 没有 LLM / prompts / 渠道凭证

本层**不引入** LLM、不读 prompts、不读任何密钥。公式系数全在 `config/feedback.yaml`，运行时加载。

## 7. 错误与回退（都不致命）

| 情况 | 怎么处理 |
|---|---|
| 无事件 / 空账本 | 空 `FeedbackResult`，`quality_weights=prior`、`is_silent=True`，不抛 |
| 某 link 无决策 | 缺省 `action="keep"`（无决策=默认留） |
| 决策 action 非法 | 加载时 `ReviewDecision`/`FeedbackEvent` 校验抛 `ValidationError`，不静默吞 |
| 某源样本 < `min_events` | 不动其权重，`diff` 仍记 `(old, old)` 以示"看过但没动" |
| `prior_weights` 缺某源 | 从 `baseline_weight` 起步 |
| 权重计算越界 | `clamp` 到 `[min_weight, max_weight]` |
| 账本文件缺/空 | `load_*` 回 `[]` / `{}` |
| 配置文件缺/空 | `FeedbackConfig()` 默认 |
| `--dry-run` | 跑 `collect→…→review`，派生本轮事件并入账本历史，算权重，把 `quality_weights` + `weight_diff` 打到 stdout，**不落盘** |

## 8. 不变量（golden 测试必须断言）

| # | 不变量 |
|---|---|
| 1 | 计数自洽：`stats.total == keep+edit+drop`，且 `sum(total) == event_count` |
| 2 | 派生覆盖：进审阅前每条 `InterpretedItem` 恰产一个事件（被删条目也有 `drop` 事件） |
| 3 | 全留升权：某源全 `keep` 且 `total >= min_events` → `new > old`（夹界内） |
| 4 | 全删降权：某源全 `drop` 且 `total >= min_events` → `new < old`（夹界内） |
| 5 | 夹界：所有 `quality_weights` 值 ∈ `[min_weight, max_weight]` |
| 6 | 样本不足不动：`total < min_events` → `new == old` |
| 7 | 历史不丢：`prior_weights` 里本轮未出现的源仍原样在 `quality_weights` |
| 8 | 聚合顺序确定：`source_stats` 按 source 字母序，与事件输入序无关 |
| 9 | 空输入 → `is_silent=True`、`quality_weights==prior`、不抛 |
| 10 | 确定性：同 `events` + 同 `prior_weights` → 同 `FeedbackResult`（含 `weight_diff`） |
| 11 | edit 弱正：某源全 `edit`（`total>=min_events`）→ `old < new <= 全 keep 同样本的 new` |
| 12 | 只算不接：本层不修改任何上游条目的 `score`/`source_type`/…（不碰打分） |

## 9. golden 用例（≥6，冻结输入 fixtures）

| # | 用例 | 断言要点 | 关联不变量 |
|---|---|---|---|
| 1 | 派生含 drop：3 条，1 条决策 drop、1 条 edit、1 条无决策 | 产 3 事件，action = drop/edit/keep | 2 |
| 2 | 按源聚合：多源混合 | keep/edit/drop/total 准，source 字母序 | 1,8 |
| 3 | 全留升权：源 A 全 keep | `A` 新权重 > 旧、≤ max | 3,5 |
| 4 | 全删降权：源 B 全 drop | `B` 新权重 < 旧、≥ min | 4,5 |
| 5 | 夹界饱和：旧权重已近 max，全 keep | 新权重 == max（不越界） | 5 |
| 6 | 样本不足：源 C 仅 1 条（`min_events=2` 场景）| `C` 权重不动，`diff[C]=(old,old)` | 6 |
| 7 | 历史保留：prior 有 D，本轮无 D 事件 | `quality_weights` 仍含 D 原值 | 7 |
| 8 | edit 弱正：源 E 全 edit | 升幅 < 全 keep 同样本 | 11 |
| 9 | 空输入 → silent | 空结果、`is_silent=True`、权重透传 | 9 |
| 10 | 确定性：打乱事件顺序两次调用 | `FeedbackResult` 全等 | 8,10 |

## 10. 测试要求

| 类型 | 测什么 |
|---|---|
| contract | `load_feedback_config`（缺文件回默认 / 覆盖字段）；`load_feedback_events` / `load_quality_weights`（缺文件回空 / 非法 action 抛错）；`FeedbackEvent` / `SourceFeedbackStats` / `FeedbackConfig` / `FeedbackResult` schema；`--feedback` CLI 形状（JSON 可序列化） |
| golden | 冻结 `InterpretedItem` + `ReviewDecision` + `events` fixtures 跑 §9 的 ≥6 用例，断言 §8 |

- 全程**纯内存、不打网络、不调 LLM、不落盘**（输入直接构造注入）；时间用注入的 `now`，层内不取 now。
- 纯函数 `derive_events` / `aggregate_by_source` / `compute_quality_weights` / `feedback` 全离线可测。

## 11. 可观察（写 `runs`）

| 事件 | 何时 | 载荷 |
|---|---|---|
| `feedback_start` | 进入 | `{run_id, event_count}` |
| `weights_computed` | 算完 | `{source_count, changed_count}`（`changed_count` = `diff` 里新旧不等的源数） |
| `feedback_done` | 结束 | `{event_count, source_count, silent}` |

## 12. 验收（对齐 PRD §2.1 / §4.5）

| 验收点 | 本层交付 |
|---|---|
| #6 反馈可回收 | review_action（留/删/改）从全量条目派生成 `FeedbackEvent`，被删条目也回收 |
| #6 可解释 | `weight_diff` 给出每源旧→新，调参/审计可读 |
| #6 能反哺 | 产出 `quality_weights`（按源信誉），**接回打分是显式未来改动**（配 ADR） |
| #1 端到端 | `collect→dedup→score→interpret→review→feedback` 串得起来，`--dry-run --feedback` 出权重 + 差异 |
| #8 静默正确 | 无事件时返回空结果、`is_silent=True`，不抛 |
| 冷启动一致（§4.3） | 未知源从 `baseline_weight` 起步；不产 `reader_relevance`（保持为 0/不参与） |
| #9 可观察 | `feedback_done` 等写入 `runs` |
