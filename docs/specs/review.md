# Spec — 审阅层 (Review)

> 放置路径：`docs/specs/review.md`。这是七层流水线的第 5 层，MVP 第五个要实现的模块。
> 对应 PRD §5.3（审阅层：每条留/删/改/拖动排序，≤10 min，审阅动作全部回收为反馈信号）、§5.5（NewsItem 模板含 `review_action: keep|drop|edit` = 隐式反馈）、§4.4（hot_take 可 AI 起草、人工定稿；无证据不进必读）、§3.4（审阅未在窗口内完成 → 进入"待审"队列不自动发）、§2.1（验收 #6 审阅页支持留/删/改/排序、单期审阅 ≤10 分钟）。
> 上游：第 4 层解读 (`docs/specs/interpret.md`) 产出的 `InterpretResult.interpreted_items: list[InterpretedItem]` + `daily_take`。下游：第 6 层发布（多端渲染器消费审阅后的 `ReviewedItem`），第 7 层反馈（消费审阅动作信号）。

## 1. 目的

把上游解读层产出的 `InterpretedItem` 列表，按人工审阅决策（留 / 删 / 改 / 排序）转化为**已审阅、可发布**的内容模型 `ReviewedItem`，并把每条的审阅动作回收为结构化反馈信号（PRD §5.3「审阅动作全部回收为反馈信号」）。

本层是"人在环路"的把关点：解读层只产 AI 草稿，**人工定稿在此**（PRD §4.4「可 AI 起草、人工定稿」）。本层成败标准是 **PRD #6：审阅页支持留/删/改/排序、单期审阅 ≤10 分钟**——本圈只实现纯核心决策应用（把决策应用到内容上），Web 审阅页延后。

## 2. 范围 / 非目标

- **做**：读审阅决策（JSON，按 `link` 索引）、逐条应用留/删/改、按决策重排序、改后重新校验（重夹 title/summary、重算必读门）、可选 `daily_take` 覆盖、无决策时透传标记"待审"、产出 `ReviewResult`（含审阅动作信号）、写 `runs` 事件、支持 `--dry-run`。
- **不做（本圈明确延后）**：
  - **Web 审阅页 / 交互 UI**（读 dry-run JSON、点选留删改、拖动排序、≤10 min 计时）：属审阅层的人机界面，本圈只做**纯核心决策应用**（决策以 JSON 注入），UI 留后续 PR（CLAUDE.md「能 50 行别写 200 行」「一次只做一层」）。
  - **反馈闭环 / 信号消费**：本层只**回收并记录**审阅动作（`review_action` / `edited_fields`），不据此更新打分权重或源信誉——那是第 7 层反馈（PRD §5.4）。YAGNI：先记录，不建环。
  - **重新解读 / 调 LLM**：本层是纯函数决策应用，**不引入任何 LLM 调用**；改写内容来自人工决策的 `edits`，不是 LLM 重生成。
  - **多端渲染 / 今日必读分组**：属第 6 层发布（PRD §5.1 一稿多渲染）。本层只标 `eligible_for_must_read`（继承/重算），不分组排版。
  - **抓全文 / 补证据**：本层只在人工 `edits` 给定的字段内改写，不抓取、不补造证据锚点（CLAUDE.md「宁可少写不可编造」）。

## 3. 接口契约

```python
def review(items: list[InterpretedItem], daily_take: str | None,
           decisions: dict[str, ReviewDecision], config: ReviewConfig,
           ctx: RunContext) -> ReviewResult: ...
```

- **输入**：
  - `items: list[InterpretedItem]` —— 上游解读条目（`InterpretResult.interpreted_items`，已按 score 降序）。
  - `daily_take: str | None` —— 上游今日看点（可被决策覆盖，见 §5.6）。
  - `decisions: dict[str, ReviewDecision]` —— 按 item `link` 索引的审阅决策；由 `load_review_decisions(config.decisions_path)` 加载（见 §6）。某 `link` 不在 dict ⇒ 默认 keep（§5.2）。
  - `config: ReviewConfig`（见 §6，字段上限 / 决策路径读 `config/review.yaml`，不写死）。
  - `ctx: RunContext` —— 复用上游 `run_id` / `now` / `logger`。
- **纯函数 / 无外部副作用（CLAUDE.md 架构约束）**：本层**不调 LLM、不打网络、不抓取**。唯一 IO = 启动时 `load_review_decisions` 读 JSON 文件（与 `load_*_config` 同风格）；`review()` 本体及 `apply_decision` / `order_reviewed` / 必读门重算皆为**纯函数**，注入冻结 `items` + 内存 `decisions` 离线确定性可测。
- **决策运行时加载**：审阅决策是内容/编辑数据，运行时从 `config.decisions_path` 读取，不硬编码（CLAUDE.md 内容纪律）。

## 4. 数据契约

```python
class ReviewDecision(BaseModel):
    action: str                          # "keep" | "drop" | "edit"
    order: int | None = None             # 可选; 重排序索引(升序); None 保持上游序
    edits: dict = {}                     # action=="edit" 时覆盖的内容字段(见 §5.4)

class ReviewedItem(InterpretedItem):     # InterpretedItem 的下游演进; 本圈加审阅字段
    review_action: str                   # "keep" | "edit"  (drop 的条目不进结果)
    was_edited: bool                     # 是否发生过字段覆盖
    edited_fields: list[str] = []        # 实际被覆盖的字段名(反馈信号)

class ReviewResult:
    reviewed_items: list[ReviewedItem]   # 保留(未删)条目, 按 §5.5 排序
    daily_take: str | None               # 今日看点(可被决策覆盖, 见 §5.6)
    input_count: int                     # 入参 items 数
    kept_count: int                      # review_action=="keep" 的条数
    dropped_count: int                   # 被删除条数
    edited_count: int                    # review_action=="edit" 的条数
    is_reviewed: bool                    # 是否存在任一显式决策(见 §5.7)
    is_pending: bool                     # 无任何决策 → 待审(不自动发, PRD §3.4)
    is_silent: bool                      # input_count == 0
```

> `kept_count + edited_count == len(reviewed_items)`（保留的条目要么纯留要么改后留）。
> `kept_count + edited_count + dropped_count == input_count`（每条要么保留要么删除，恒不漏账）。
> `review_action` 仅取 `keep` / `edit`；`drop` 条目不进 `reviewed_items`（其删除事实记于 `dropped_count` 与 §11 事件）。

## 5. 算法（确定性 / IO 隔离）

### 5.1 空输入短路

`items == []` → 返回 `ReviewResult(reviewed_items=[], daily_take=daily_take, input_count=0, kept_count=0, dropped_count=0, edited_count=0, is_reviewed=False, is_pending=True, is_silent=True)`，不抛异常（PRD §3.4 静默；上游空则本层空）。

### 5.2 单条决策应用（`apply_decision(item, decision, config) -> ReviewedItem | None`，纯函数）

对每个 `InterpretedItem`，按其 `link` 取 `decisions.get(link)`：

1. **无决策**（`link` 不在 dict）⇒ 视为 **keep**：原样透传，`review_action="keep"`、`was_edited=False`、`edited_fields=[]`。
2. **action=="keep"** ⇒ 同上，原样保留。
3. **action=="drop"** ⇒ 返回 `None`（该条从结果移除，计入 `dropped_count`）。
4. **action=="edit"** ⇒ 见 §5.4。

未知 `action`（非 keep/drop/edit）⇒ schema 校验在 `ReviewDecision` 构造时即拒（pydantic 限定枚举），不进算法。

### 5.3 删除（drop）

`apply_decision` 返回 `None` ⇒ orchestrator 跳过该条、`dropped_count += 1`、emit `item_dropped{link}`（§11）。删除是人工显式动作，**不影响其它条**。

### 5.4 改写（edit）+ 改后重新校验

`action=="edit"` 时，只允许覆盖**内容字段**，provenance/出处字段只读：

- **可改内容字段**：`title` / `summary` / `takeaway` / `hot_take` / `tags` / `evidence`。
- **只读 provenance**（不得被 `edits` 覆盖；若 `edits` 含这些键则**忽略**）：`score` / `score_breakdown` / `source` / `source_type` / `link` / `published_at` / `cluster_id` / `related_links` / `title_en` / `raw_summary` / `is_explore`。
- 逐字段：`edits` 中存在该键则覆盖，并记入 `edited_fields`；不存在则保留解读层原值。
- **改后重新校验**（纯函数，复用 §config 上限）：
  - `title` 重夹到 `title_max_chars`、`summary` 重夹到 `summary_max_chars`。
  - `evidence` 重新过滤 `anchor ∈ {item.link} ∪ set(item.related_links)`（人工也不得编造锚点；非法锚点丢弃）。
  - **重算必读门**（§5.8）：`eligible_for_must_read` 据改后的 `evidence` / `takeaway` / `interpretation_status` 重新派生。
- `review_action="edit"`、`was_edited=(edited_fields != [])`。
  - 边界：`action=="edit"` 但 `edits` 为空或未改动任何字段 ⇒ `was_edited=False`、`edited_fields=[]`，仍计 `edited_count`（人工显式标记过 edit）。

### 5.5 排序与确定性（`order_reviewed`）

保留条目重排序规则：

- 决策含 `order`（int）的条目按 `order` 升序在前；`order` 相同用上游 score 序兜底。
- 无 `order` 的条目保持上游序（score 降序，同分 `published_at` 升序、`link` 升序——与解读层 §5.5 一致）。
- 稳定排序：`(has_order desc, order asc, upstream_index asc)`，保证同一输入 + 同一 decisions ⇒ 同顺序（确定性）。

### 5.6 今日看点覆盖（可选）

约定保留键 `decisions["__daily_take__"]`（sentinel，不对应任何真实 item）：若该键存在且为 `action=="edit"` 且 `edits` 含 `daily_take`，则 `ReviewResult.daily_take = edits["daily_take"]`（人工设为空串 `""` 视为清空/不发今日看点）；否则透传上游 `daily_take`。该 sentinel 仅用于 daily_take 覆盖，不进 `reviewed_items`、不计入 kept/dropped/edited。

### 5.7 是否已审 / 待审

```
is_reviewed = (任一 item 命中显式 decisions 决策, 或存在 __daily_take__ 覆盖)
is_pending  = (not is_reviewed) and (input_count > 0)
```

- **无任何决策**：所有条目透传 keep、`is_reviewed=False`、`is_pending=True`——产物**可发布但标记"待审"**（PRD §3.4「审阅未在窗口内完成 → 进入待审队列、不自动发」；本层只置位，发布层据 `is_pending` 决定是否拦截）。
- **有决策**：`is_reviewed=True`、`is_pending=False`。

### 5.8 必读门重算（派生 `eligible_for_must_read`，PRD §4.4「无证据不进必读」）

```
eligible_for_must_read = (interpretation_status == "ok")
                         and (len(evidence) >= config.min_evidence)
                         and (takeaway != "")
```

与解读层 §5.4 同式。keep 条目沿用上游值（未改则等价）；edit 条目在改后字段上**重新派生**——人工删空 evidence/takeaway ⇒ 自动降级出必读；人工补合法 evidence + takeaway ⇒ 可升级（但 `interpretation_status` 只读，回退条目不能被人工"洗白"成 ok）。

## 6. 配置与 provider

### 6.1 `config/review.yaml`

```yaml
decisions_path: "data/review_decisions.json"  # 审阅决策(按 link 索引); 缺则全 keep/待审
title_max_chars: 64                  # 与解读层一致(PRD §5.5)
summary_max_chars: 120               # 与解读层一致
tags_count: 3                        # 改后若涉 tags 的一致性参考(本层不强制回退)
min_evidence: 1                      # 必读门: 至少 1 条证据(§5.8)
```

对应 `ReviewConfig`（dataclass，默认值与上表一致）：`decisions_path: str`、`title_max_chars: int`、`summary_max_chars: int`、`tags_count: int`、`min_evidence: int`。加载器 `load_review_config(path)` 与 `load_interpret_config` 同风格（缺文件 → 默认值；`.get` per field）。

### 6.2 决策加载器

```python
def load_review_decisions(path: str) -> dict[str, ReviewDecision]:
    """Read审阅决策 JSON(按 link 索引); 缺文件 → {} (全 keep/待审)。"""
    ...
```

- JSON 结构：`{"<link>": {"action": "keep|drop|edit", "order": int|null, "edits": {...}}, ...}`；可含保留键 `__daily_take__`（§5.6）。
- 缺文件 / 空文件 ⇒ 返回 `{}`（等价无决策、全 keep、`is_pending=True`），不抛（PRD 静默友好）。
- 每个 value 经 `ReviewDecision` pydantic 校验（非法 `action` 即拒）。

### 6.3 无 prompts / 无 LLM provider

本层**不引入** LLM / prompts（区别于解读层）。决策数据即"内容 SOP 的人工产物"，运行时从 `decisions_path` 加载。

## 7. 错误与回退（非致命，继承 CLAUDE.md/PRD §3.4）

| 情况 | 处理 |
|---|---|
| 入参 `items == []`（上游 `is_silent`） | 返回空 `ReviewResult`（`is_silent=True`、`is_pending=True`），不抛 |
| 决策文件缺失 / 空 | `decisions={}` → 全 keep、`is_reviewed=False`、`is_pending=True`（待审，不自动发） |
| 决策 JSON 含未知 `action` | `ReviewDecision` 校验失败 → 该条决策视为非法、按 keep 兜底（不删不改），emit 告警事件 |
| `edits` 含只读 provenance 字段 | 忽略这些键（不覆盖出处），只应用可改内容字段（§5.4） |
| edit 后 `evidence.anchor` 不在 `link∪related_links` | 丢弃该条 evidence（人工也不得编造锚点）；清空后据必读门降级 |
| edit 把 `takeaway`/`evidence` 删空 | `eligible_for_must_read=False`（无证据不进必读） |
| 决策 `link` 在 items 中不存在 | 忽略该决策（无对应条目可应用） |
| `--dry-run` | 链路 `collect()→dedup()→score()→interpret()→review()`；产 `ReviewResult` JSON |

## 8. 不变量（golden 测试必须断言）

1. **账目守恒**：`kept_count + edited_count + dropped_count == input_count`；`kept_count + edited_count == len(reviewed_items)`（不漏账、不重复）。
2. **drop 即移除**：`action=="drop"` 的条目不出现在 `reviewed_items`，且 `dropped_count` 准确计数。
3. **provenance 只读**：任一 `ReviewedItem` 的 `score`/`source`/`link`/`published_at`/`cluster_id`/`title_en`/`raw_summary` 等出处字段恒等于上游 `InterpretedItem`（edit 不能改出处）。
4. **必读门重算**：`eligible_for_must_read == (interpretation_status=="ok" ∧ len(evidence)≥min_evidence ∧ takeaway≠"")`；edit 删空 evidence/takeaway ⇒ `False`。
5. **不能洗白回退**：`interpretation_status` 只读；上游 `extractive_fallback` 的条目经 edit 后 `interpretation_status` 仍为 `extractive_fallback`，不因人工编辑变 `ok`（即使补了 evidence/takeaway，仍因 status≠ok 不入必读）。
6. **证据锚点合法**：edit 后每条 `evidence.anchor ∈ item.link ∪ item.related_links`（非法锚点已丢弃）。
7. **待审标记**：无任何决策 ⇒ `is_reviewed==False`、`is_pending==True`、全条 `review_action=="keep"`、`reviewed_items` 顺序==上游顺序。
8. **确定性**：同一输入 + 同一 decisions ⇒ 同 `reviewed_items` 字段 / 同顺序 / 同计数 / 同 `daily_take`。
9. **排序**：含 `order` 的条目按 `order` 升序在前，无 `order` 的保持上游 score 序（§5.5）。
10. 入参 `[]` → 空 `ReviewResult`、`is_silent==True`、不抛异常。
11. 每个 `ReviewedItem` 继承全部 `InterpretedItem`/`ScoredItem`/`NewsItem`/`RawItem` 不变量（score∈[0,100]、breakdown 9 键、cluster_id 非空、evidence 锚点合法等）。

## 9. golden 用例（fixtures 驱动，≥6）

> 用**冻结的 InterpretedItem fixtures** + 内存 `decisions` dict，使审阅确定、可断言，不依赖文件/网络。

1. **透传待审**：`decisions={}` → 全条 `review_action=="keep"`、`is_reviewed==False`、`is_pending==True`、顺序==上游、`kept_count==input_count`、`dropped_count==0`（不变量 7）。
2. **删除生效**：对某 `link` 给 `action=="drop"` → 该条不在 `reviewed_items`、`dropped_count==1`、账目守恒（不变量 1、2）。
3. **改写 + 重夹 + 重算门**：`edit` 覆盖超长 `title`/`summary` → 被重夹到上限；改 `takeaway`+合法 `evidence` → `eligible_for_must_read==True`；`review_action=="edit"`、`edited_fields` 含被改字段（不变量 4）。
4. **改写不能洗白回退**：上游 `extractive_fallback` 条目 edit 补 evidence/takeaway → `interpretation_status` 仍 `extractive_fallback`、`eligible_for_must_read==False`（不变量 5）。
5. **edit 非法锚点丢弃**：`edit` 的 `evidence.anchor` 不在 `link∪related_links` → 丢弃、`eligible_for_must_read==False`（不变量 6）。
6. **重排序**：给两条 `order`（如 `order=0`/`order=1` 反转上游序）→ `reviewed_items` 顺序按 `order`，无 `order` 的尾随上游序（不变量 9）；重复调用一致（不变量 8）。
7. **空输入 → silent**：`items==[]` → 空 `ReviewResult`、`is_silent==True`、`is_pending==True`（不变量 10）。
8. **今日看点覆盖**：`__daily_take__` edit 给新 `daily_take` → `ReviewResult.daily_take` 为新值；无覆盖则透传上游（§5.6）。
9. **provenance 只读**：`edit` 的 `edits` 含 `score`/`link` → 被忽略，输出出处字段恒等上游（不变量 3）。

## 10. 测试要求

- **contract**：`load_review_config` 加载（缺文件回默认 / 覆盖字段）；`load_review_decisions` 读取（缺文件→`{}` / 正常解析 / 非法 action 拒绝）；`ReviewDecision`/`ReviewedItem`/`ReviewResult` schema 校验（action 枚举、ReviewedItem 继承 InterpretedItem 不变量）。
- **golden**：用冻结 fixtures + 内存 decisions 驱动 §9 的 ≥6 个用例，断言 §8 不变量。
- 全程**纯内存、不打网络、不调 LLM、不读真实文件**（decisions 直接构造 dict 注入 `review()`，文件加载单独在 contract 测）；时间用注入的 `ctx.now`。
- 纯函数 `apply_decision`/`order_reviewed`/必读门重算/字段重夹全程离线可测。

## 11. 可观察

- 每条保留 emit `item_kept{link, edited: bool}`（复用 interpret/score 的 `emit`）。
- 每条删除 emit `item_dropped{link}`。
- 每条改写 emit `item_edited{link, edited_fields}`。
- `review()` 结束 emit `review_done{input_count, kept_count, dropped_count, edited_count, is_pending, silent}`，写入 `runs`。

## 12. 验收（对齐 PRD §2.1）

- **#6 审阅可操作**：本圈实现留/删/改/排序的**核心决策应用**（`review()` 据 decisions 产 `ReviewedItem`）；Web 页 ≤10 min 计时延后，但数据契约（按 link 索引的四操作决策）为其备好。
- **#5 解读零幻觉延续**：edit 后必读门重算，**无证据/无 takeaway 条目 `eligible_for_must_read==False`**；人工不能编造锚点（非法 anchor 丢弃）、不能洗白回退条目（status 只读）。
- **#1 端到端**：`collect()→dedup()→score()→interpret()→review()` 可串联，`--dry-run --review` 产 `ReviewResult` JSON。
- **#8 静默正确**：上游静默时本层返回空结果（`is_silent=True`），不抛异常。
- **待审正确**（PRD §3.4）：无审阅决策时 `is_pending=True`，发布层据此可拦截"未审自动发"。
- **#9 可观察**：`review_done` 等事件写入 `runs`，审阅动作（keep/drop/edit + edited_fields）回收为反馈信号供第 7 层消费。
