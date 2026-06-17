# Spec — 审阅层 (Review)

> 路径：`docs/specs/review.md`。七层流水线第 5 层，MVP 第五个模块。
> 上游：第 4 层解读产出的 `interpreted_items` + `daily_take`。下游：第 6 层发布、第 7 层反馈。
> 对应 PRD 条款：

| PRD | 说的事 | 本层怎么落 |
|---|---|---|
| §5.3 | 审阅页每条可"留/删/改/拖动排序"，≤10 min，动作全部回收成反馈 | 本圈做"应用决策"的核心，UI 延后；动作记进 `ReviewResult` |
| §5.5 | NewsItem 模板带 `review_action: keep\|drop\|edit`（隐式反馈） | 每条输出带 `review_action` + 改了哪些字段 |
| §4.4 | hot_take 可 AI 起草、人工定稿；无证据不进必读 | 解读层只产草稿，人在这一层定稿；改完重算必读门 |
| §3.4 | 没审完不自动发，进"待审"队列 | 没有任何决策 → `is_pending=True` |
| §2.1 #6 | 审阅支持留/删/改/排序，单期 ≤10 分钟 | 本圈备好四操作的数据契约 |

## 1. 目的

一句话：**把解读层的 AI 草稿，按人工的"留/删/改/排序"决定，变成可以发布的定稿。**

这是"人在环路"的把关点——解读层产的是草稿，到这一层由人拍板。同时把每个人工动作（留了什么、删了什么、改了哪些字段）记下来，喂给第 7 层做反馈。

本圈**只做核心逻辑**（决策怎么应用到条目上），审阅用的网页延后再做。

## 2. 范围 / 非目标

**做：**

| 能力 | 说明 |
|---|---|
| 读决策 | 从 JSON 读人工决策，按文章 `link` 对号入座 |
| 留/删/改 | 逐条应用：留=原样，删=移走，改=覆盖指定字段 |
| 排序 | 按决策里的 `order` 重排，没给的保持上游顺序 |
| 改后校验 | 改完重新夹长度、重算"能否进必读" |
| 今日看点覆盖 | 人工可改写或清空 `daily_take` |
| 待审标记 | 一条决策都没有 → 标记"待审"，不拦发布但置位提醒 |
| 产物 + 留痕 | 产 `ReviewResult`，写 `runs` 事件，支持 `--dry-run` |

**不做（这一圈明确延后）：**

| 不做 | 为什么 / 归属 |
|---|---|
| 网页审阅 UI（点选、拖动、计时） | 本圈只做核心，决策用 JSON 喂进来；UI 留后续 PR |
| 反馈闭环（拿动作去调权重/源信誉） | 只**记录**动作，不消费；那是第 7 层。先记不建环（YAGNI） |
| 调 LLM 重写内容 | 本层纯函数，改写内容全来自人工 `edits`，不重新生成 |
| 多端渲染 / 必读 Top3 分组 | 第 6 层发布的事；本层只**标**资格不分组 |
| 抓全文 / 自动补证据 | 只在人工给的字段里改，绝不抓取、不编锚点 |

## 3. 接口契约

```python
def review(items: list[InterpretedItem], daily_take: str | None,
           decisions: dict[str, ReviewDecision], config: ReviewConfig,
           ctx: RunContext) -> ReviewResult: ...
```

**输入：**

| 参数 | 是什么 |
|---|---|
| `items` | 上游解读条目（已按 score 降序） |
| `daily_take` | 上游今日看点，可被覆盖（§5.6） |
| `decisions` | 按文章 `link` 索引的决策表；`load_review_decisions()` 读出来 |
| `config` | 字段上限 / 决策路径，读 `config/review.yaml`，不写死 |
| `ctx` | 复用上游 `run_id` / `now` / `logger` |

**纪律（CLAUDE.md 架构约束）：**

- **不调 LLM、不打网络、不抓取。** 唯一的 IO 是启动时读一次决策 JSON。
- `review()` 本体和 `apply_decision` / `order_reviewed` / 必读门重算全是**纯函数**，注入冻结 `items` + 内存 `decisions` 就能离线确定性测。
- 决策是内容数据，运行时从 `config.decisions_path` 读，不硬编码。

## 4. 数据契约

```python
class ReviewDecision(BaseModel):
    action: str = "keep"        # keep | drop | edit
    order: int | None = None    # 重排序号(升序); None=不指定
    edits: dict = {}            # action==edit 时要覆盖的字段(见 §5.4)

class ReviewedItem(InterpretedItem):   # 在解读条目上加审阅痕迹
    review_action: str          # keep | edit  (drop 的不进结果)
    was_edited: bool            # 有没有真的改过字段
    edited_fields: list[str] = []   # 改了哪些字段(反馈信号)

class ReviewResult:
    reviewed_items: list[ReviewedItem]  # 留下的条目, 已排序
    daily_take: str | None      # 今日看点(可被覆盖)
    input_count: int            # 进来几条
    kept_count: int             # 纯留几条
    dropped_count: int          # 删了几条
    edited_count: int           # 改了几条
    is_reviewed: bool           # 有没有任何人工决策
    is_pending: bool            # 没决策 → 待审, 不自动发
    is_silent: bool             # 进来 0 条
```

**计数恒等式（必须永远成立）：**

| 等式 | 含义 |
|---|---|
| `kept + edited + dropped == input_count` | 每条都有去向，不漏账 |
| `kept + edited == len(reviewed_items)` | 留下的=纯留+改留 |

> `review_action` 只会是 `keep` 或 `edit`；`drop` 的条目不进 `reviewed_items`，只记进 `dropped_count` 和事件。

## 5. 算法（确定性 / 无 IO）

### 5.1 空输入直接返回

`items == []` → 返回空 `ReviewResult`，`is_silent=True`、`is_pending=True`，不抛异常。上游静默，本层也静默。

### 5.2 逐条决策（`apply_decision(item, decision, config) -> ReviewedItem | None`）

对每条按 `link` 找决策，照下表处理：

| 情况 | 动作 | 结果 |
|---|---|---|
| `link` 不在决策表 | 默认 keep | 原样透传，`review_action="keep"` |
| `action == "keep"` | 留 | 原样透传 |
| `action == "drop"` | 删 | 返回 `None`，计入 `dropped_count` |
| `action == "edit"` | 改 | 见 §5.4 |

> 非法 `action`（不是这三个）在构造 `ReviewDecision` 时就被 pydantic 拒掉，进不了算法。

### 5.3 删除（drop）

`apply_decision` 返回 `None` → orchestrator 跳过该条、`dropped_count += 1`、emit `item_dropped`。删除是显式动作，不影响别的条。

### 5.4 改写（edit）

**只能改内容，不能改出处。** 哪些能改：

| 类别 | 字段 | 能改？ |
|---|---|---|
| 内容（可改） | `title` `summary` `takeaway` `hot_take` `tags` `evidence` | ✅ `edits` 里给了就覆盖 |
| 出处（只读） | `score` `score_breakdown` `source` `genre` `publisher` `link` `published_at` `cluster_id` `related_links` `title_en` `raw_summary` `is_explore` | ❌ `edits` 里给了也忽略 |

**改完要重新校验：**

| 步骤 | 做什么 |
|---|---|
| 重夹长度 | `title` 夹到 `title_max_chars`、`summary` 夹到 `summary_max_chars` |
| 过滤证据 | `evidence.anchor` 必须在 `link ∪ related_links` 里，非法的丢掉（人工也不能编锚点） |
| 重算必读门 | 据改后的 evidence / takeaway / status 重新派生（§5.8） |

`review_action="edit"`；`was_edited` 看实际有没有改动（`edited_fields` 非空才 True）。

> 边界：标了 `edit` 但 `edits` 为空或没改动任何字段 → `was_edited=False`、`edited_fields=[]`，但仍计入 `edited_count`（人工确实点了"改"）。

### 5.5 排序（`order_reviewed`）

| 条目 | 排在哪 |
|---|---|
| 决策给了 `order` | 按 `order` 升序排在前面 |
| 没给 `order` | 保持上游顺序（score 降序，同分按 `published_at`、`link` 兜底） |

排序键 `(有order优先, order升序, 上游下标升序)`，稳定排序 → 同输入同决策必同顺序。

### 5.6 今日看点覆盖（可选）

用一个保留键 `decisions["__daily_take__"]`（占位，不对应真实文章）：

| 决策表里 | `ReviewResult.daily_take` |
|---|---|
| 有 `__daily_take__` 且 `edits` 含 `daily_take` | 用新值（设成 `""` 即清空，等于不发今日看点） |
| 没有 | 透传上游 `daily_take` |

这个占位键只管 daily_take，不进 `reviewed_items`、不计 kept/dropped/edited。

### 5.7 已审 / 待审

```
is_reviewed = 有任一条命中决策, 或有 __daily_take__ 覆盖
is_pending  = (没有 is_reviewed) 且 (input_count > 0)
```

| 场景 | `is_reviewed` | `is_pending` | 含义 |
|---|---|---|---|
| 一条决策都没有 | False | True | 全透传 keep，标"待审"，发布层据此决定拦不拦 |
| 有任何决策 | True | False | 已审过 |

### 5.8 必读门重算（派生 `eligible_for_must_read`）

```
eligible_for_must_read = (interpretation_status == "ok")
                         and (len(evidence) >= config.min_evidence)
                         and (takeaway != "")
```

和解读层 §5.4 同一条式子。keep 条目沿用上游值；edit 条目用改后字段重算：

- 人工删空 evidence/takeaway → 自动掉出必读。
- 人工补合法 evidence + takeaway → 可升级，**但** `interpretation_status` 只读，回退条目（`extractive_fallback`）洗不白成 `ok`，照样进不了必读。

## 6. 配置与加载

### 6.1 `config/review.yaml`

```yaml
decisions_path: "data/review_decisions.json"  # 决策表; 缺则全 keep/待审
title_max_chars: 64        # 与解读层一致
summary_max_chars: 120     # 与解读层一致
tags_count: 3              # 一致性参考(本层不因 tags 数强制回退)
min_evidence: 1            # 必读门: 至少 1 条证据
```

`ReviewConfig`（dataclass，默认值同上）：`decisions_path` / `title_max_chars` / `summary_max_chars` / `tags_count` / `min_evidence`。加载器 `load_review_config(path)` 与 `load_interpret_config` 同风格（缺文件→默认；`.get` per field）。

### 6.2 决策加载器

```python
def load_review_decisions(path: str) -> dict[str, ReviewDecision]:
    """读决策 JSON(按 link 索引); 缺文件 → {}(全 keep/待审)。"""
```

JSON 长这样：

```json
{
  "https://a/1": {"action": "drop"},
  "https://a/2": {"action": "edit", "order": 0, "edits": {"title": "新标题"}},
  "__daily_take__": {"action": "edit", "edits": {"daily_take": "人工改写的看点"}}
}
```

- 缺文件 / 空文件 → 返回 `{}`（等于全 keep、待审），不抛。
- 每个 value 过 `ReviewDecision` 校验，非法 `action` 即拒。

### 6.3 没有 LLM / prompts

本层**不引入** LLM、不读 prompts（这点和解读层不同）。决策 JSON 就是"人工产出的内容"，运行时加载。

## 7. 错误与回退（都不致命）

| 情况 | 怎么处理 |
|---|---|
| 进来 0 条 | 空 `ReviewResult`，`is_silent=True`、`is_pending=True`，不抛 |
| 决策文件缺/空 | `decisions={}` → 全 keep、待审 |
| 决策含未知 `action` | 校验失败 → 该条按 keep 兜底（不删不改），emit 告警 |
| `edits` 想改出处字段 | 忽略那些键，只应用可改的内容字段 |
| edit 后锚点非法 | 丢掉那条 evidence；清空后据必读门降级 |
| edit 把 takeaway/evidence 删空 | `eligible_for_must_read=False` |
| 决策的 `link` 没对应文章 | 忽略该决策 |
| `--dry-run` | 跑 `collect→dedup→score→interpret→review`，产 `ReviewResult` JSON |

## 8. 不变量（golden 测试必须断言）

| # | 不变量 |
|---|---|
| 1 | 账目守恒：`kept+edited+dropped == input_count`；`kept+edited == len(reviewed_items)` |
| 2 | drop 即移除：被删的不在 `reviewed_items`，`dropped_count` 计数准 |
| 3 | 出处只读：`score`/`source`/`link`/`published_at`/`cluster_id`/`title_en`/`raw_summary` 恒等上游 |
| 4 | 必读门重算：`eligible == (status=="ok" ∧ len(evidence)≥min_evidence ∧ takeaway≠"")` |
| 5 | 洗不白：edit 后 `interpretation_status` 仍是上游值，回退条目改完仍进不了必读 |
| 6 | 锚点合法：edit 后每条 `evidence.anchor ∈ link ∪ related_links` |
| 7 | 待审：无决策时 `is_reviewed=False`、`is_pending=True`、全 `keep`、顺序==上游 |
| 8 | 确定性：同输入同决策 → 同字段 / 同顺序 / 同计数 / 同 `daily_take` |
| 9 | 排序：有 `order` 的按 `order` 升序在前，没给的保持上游序 |
| 10 | 空输入 → 空 `ReviewResult`、`is_silent=True`、不抛 |
| 11 | `ReviewedItem` 继承全部上游不变量（score∈[0,100]、breakdown 9 键、cluster_id 非空等） |

## 9. golden 用例（≥6，冻结 fixtures + 内存 decisions）

| # | 用例 | 断言要点 | 关联不变量 |
|---|---|---|---|
| 1 | 全透传待审：`decisions={}` | 全 `keep`、`is_pending=True`、顺序==上游 | 7 |
| 2 | 删除生效：某条 `drop` | 不在结果、`dropped_count==1`、账目守恒 | 1, 2 |
| 3 | 改写+重夹+重算门：超长 title/summary + 改 takeaway/evidence | 被夹到上限、`eligible=True`、`edited_fields` 准 | 4 |
| 4 | 洗不白回退：回退条目 edit 补证据 | `status` 仍 `extractive_fallback`、`eligible=False` | 5 |
| 5 | 非法锚点丢弃：edit 给的 anchor 不合法 | 丢掉、`eligible=False` | 6 |
| 6 | 重排序：两条给反转的 `order` | 按 `order` 排，没给的尾随上游序；重复调用一致 | 8, 9 |
| 7 | 空输入 → silent | 空结果、`is_silent=True` | 10 |
| 8 | 今日看点覆盖：`__daily_take__` edit | `daily_take` 为新值；无覆盖则透传 | — |
| 9 | 出处只读：`edits` 含 `score`/`link` | 被忽略，出处字段恒等上游 | 3 |

## 10. 测试要求

| 类型 | 测什么 |
|---|---|
| contract | `load_review_config`（缺文件回默认/覆盖字段）；`load_review_decisions`（缺文件→`{}`/正常解析/非法 action 拒绝）；`ReviewDecision`/`ReviewedItem`/`ReviewResult` schema |
| golden | 冻结 fixtures + 内存 decisions 跑 §9 的 ≥6 用例，断言 §8 |

- 全程**纯内存、不打网络、不调 LLM、不读真实文件**（decisions 直接构造 dict 注入；文件加载单独在 contract 测）；时间用注入的 `ctx.now`。
- 纯函数 `apply_decision` / `order_reviewed` / 必读门重算 / 字段重夹全离线可测。

## 11. 可观察（写 `runs`）

| 事件 | 何时 | 载荷 |
|---|---|---|
| `item_kept` | 每条保留 | `{link, edited}` |
| `item_dropped` | 每条删除 | `{link}` |
| `item_edited` | 每条改写 | `{link, edited_fields}` |
| `review_done` | 结束 | `{input_count, kept_count, dropped_count, edited_count, is_pending, silent}` |

## 12. 验收（对齐 PRD §2.1）

| 验收点 | 本层交付 |
|---|---|
| #6 审阅可操作 | 留/删/改/排序的核心决策应用全实现；Web 计时页延后，但四操作数据契约备好 |
| #5 解读零幻觉延续 | edit 后重算必读门：无证据/无 takeaway → 进不了必读；人工不能编锚点、不能洗白回退 |
| #1 端到端 | `collect→dedup→score→interpret→review` 串得起来，`--dry-run --review` 产 JSON |
| #8 静默正确 | 上游静默时返回空结果，不抛 |
| 待审正确（§3.4） | 无决策 → `is_pending=True`，发布层可拦"未审自动发" |
| #9 可观察 | `review_done` 等写入 `runs`，审阅动作回收成反馈供第 7 层用 |
