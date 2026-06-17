# Spec — 发布层 (Publish)

> 路径：`docs/specs/publish.md`。七层流水线第 6 层，MVP 第六个模块。
> 上游：第 5 层审阅产出的 `ReviewResult`（`reviewed_items` + `daily_take` + `is_pending`）。下游：第 7 层反馈。
> 对应 PRD 条款：

| PRD | 说的事 | 本层怎么落 |
|---|---|---|
| §5.1 | 一稿多渲染：一个内容模型 → Notion / 公众号 HTML / 网站 JSON / RSS 多渲染器 | 本圈做"内容模型 → Markdown 渲染器"一条，多渠道渲染器延后；内容模型为 P1 预留 |
| §5.2 | 日报结构：今日看点 + 今日必读 Top3 + 分类速览 + 数据概览 | 内容模型四块对齐；Markdown 按 §5.6 模板渲染 |
| §5.6 | 整份日报 Markdown 模板 | 渲染器输出对齐该模板，snapshot 锁定 |
| §3.4 | 没审完不自动发，进"待审"队列 | `is_pending=True` 时照常渲染但打水印；发不发交上层 |
| §2.1 #7 | 发布成功率 ≥ 95%，至少发到 1 个渠道；某渠道失败不影响其他 | 本圈渲染产物即"可发布稿"；真正多渠道推送与失败隔离延后 P1 |

## 1. 目的

一句话：**把审阅层定稿的条目，组装成一份结构化日报，再渲染成可发布的 Markdown。**

发布层做两件事，且分开：先把 `ReviewResult` **组装**成统一内容模型 `DailyReport`（今日看点 / 今日必读 Top3 / 分类速览 / 数据概览），再把模型**渲染**成 Markdown 字符串。组装与渲染解耦，正是 PRD §5.1 的"一稿多渲染"——本圈只落地 Markdown 渲染器，P1 复用同一个 `DailyReport` 加 JSON / Notion / 公众号渲染器。

本圈**只做核心逻辑**（组装 + Markdown 渲染），真正推送到外部渠道延后。

## 2. 范围 / 非目标

**做：**

| 能力 | 说明 |
|---|---|
| 组装内容模型 | `ReviewResult` → `DailyReport`（四块结构 + 元信息） |
| 必读 Top3 | 从 `eligible_for_must_read=True` 的条目按上游序取前 N（默认 3） |
| 分类速览 | 按 `genre` 分组，组间按既定 genre 顺序，组内保上游序 |
| 数据概览 | 类型分布计数 + 高频关键词（聚合 `tags`） |
| Markdown 渲染 | 按 PRD §5.6 模板渲染成字符串 |
| 待审水印 | `is_pending=True` 时报头打"未审草稿"水印 |
| 空输入静默 | 没有条目 → `is_silent=True`，不渲染骨架 |
| 产物 + 留痕 | 产 `PublishResult`，写 `runs` 事件，天然 `--dry-run`（产物即字符串） |

**不做（这一圈明确延后）：**

| 不做 | 为什么 / 归属 |
|---|---|
| 真正推送 Notion / 公众号 / 网站 / RSS | 那是外部副作用渠道，需 adapter + mock API；本圈纯核心，P1 再做 |
| HTML / JSON / RSS 渲染器 | 同上，P1 复用 `DailyReport` 加渲染器 |
| 多渠道失败隔离 / 重试 / 补发 | 依赖真实渠道，P1 与渠道 adapter 一起做 |
| "未审自动发"的拦截决策 | 本层只**标** `is_pending` 并打水印，发不发交 CLI / P1 渠道层 |
| 抓全文 / 调 LLM / 补内容 | 本层纯函数，只重排呈现上游已定稿的字段，不生成、不抓取 |
| outcome 回收（open/dwell/forward） | 第 7 层反馈的事 |

## 3. 接口契约

```python
def publish(review_result: ReviewResult, date_label: str,
            config: PublishConfig, ctx: RunContext) -> PublishResult: ...
```

**输入：**

| 参数 | 是什么 |
|---|---|
| `review_result` | 上游审阅产物（`reviewed_items` 已排序、`daily_take`、`is_pending`、`is_silent`） |
| `date_label` | 日报日期标签（如 `2026-05-30（周六）`）；由调用方/`ctx.now` 派生后注入，**不在层内取 now** |
| `config` | 必读条数 / 类型标签 / 水印文案 / 关键词数，读 `config/publish.yaml`，不写死 |
| `ctx` | 复用上游 `run_id` / `now` / `logger` |

**纪律（CLAUDE.md 架构约束）：**

- **不调 LLM、不打网络、不抓取、不写外部渠道。** 本圈唯一产物是内存里的字符串。
- `publish()` 本体和 `select_must_read` / `group_by_category` / `build_overview` / `build_report` / `render_markdown` 全是**纯函数**，注入冻结 `ReviewResult` 就能离线确定性测。
- 渲染所需的展示常量（类型中文名、水印文案、必读条数、关键词数）全读 `config`，不硬编码散落。

## 4. 数据契约

```python
class Overview(BaseModel):
    genre_distribution: dict[str, int]   # {"paper": 2, "model": 1, ...} 按类型计数
    keywords: list[str]                 # 高频关键词(聚合 tags), 取 Top N

class CategorySection(BaseModel):
    genre: str                          # paper | model | announcement | writeup | news
    label: str                          # 中文展示名: 论文 / 模型 / 工具 ...
    items: list[ReviewedItem]           # 该类目下条目, 保上游序

class DailyReport(BaseModel):
    date_label: str                     # 日期标签
    daily_take: str | None              # 今日看点(可为 None=不展示该块)
    must_read: list[ReviewedItem]       # 今日必读, ≤ must_read_count
    categories: list[CategorySection]   # 分类速览(全量目录)
    overview: Overview                  # 数据概览
    is_pending: bool                    # 透传自 ReviewResult; True=未审草稿
    item_count: int                     # 总条数
    explore_count: int                  # 探索性选题条数(is_explore=True)

class PublishResult:                    # dataclass
    report: DailyReport                 # 组装好的内容模型
    markdown: str                       # 渲染产物("" 当静默)
    is_pending: bool                    # 透传, 供上层决定发不发
    is_silent: bool                     # 上游空 → True
```

**不变式：**

| 等式 | 含义 |
|---|---|
| `len(must_read) <= config.must_read_count` | 必读不超额 |
| `sum(len(c.items) for c in categories) == item_count` | 速览是全量目录，每条都归类，不漏 |
| `sum(overview.genre_distribution.values()) == item_count` | 类型分布计数等于总条数 |
| `must_read ⊆ ⋃ categories.items` | 必读条目同时出现在其类型分组里（速览是全量目录，PRD §5.6） |

## 5. 算法（确定性 / 无 IO）

### 5.1 空输入直接返回

`review_result.reviewed_items == []`（或上游 `is_silent`）→ 返回 `PublishResult`，`markdown=""`、`is_silent=True`，`report` 为空骨架（四块皆空、计数 0），不渲染、不抛。上游静默，本层也静默。

### 5.2 必读挑选（`select_must_read(items, config) -> list[ReviewedItem]`）

| 步骤 | 做什么 |
|---|---|
| 过滤 | 只保留 `eligible_for_must_read == True` 的条目（无证据 / 回退条目天然排除，对齐 PRD §4.4） |
| 取前 N | 按上游顺序（已被 review 按 score 降序 + 人工 order 排过）取前 `must_read_count` 条 |
| 不足则少放 | 合格条目 < N 时有几条放几条，不补位、不拉低门槛 |

### 5.3 分类速览（`group_by_category(items, config) -> list[CategorySection]`）

| 规则 | 说明 |
|---|---|
| 分组键 | `item.genre`（值如 `paper`/`model`/…） |
| 组间顺序 | 按 `config.genre_labels` 声明的 genre 顺序（缺省顺序见 §6.1）；不在表里的 genre 排末尾 |
| 组内顺序 | 保上游序（不再二次排序） |
| 空类目 | 没有条目的类型不产 `CategorySection` |
| 标签 | `label` 取 `config.genre_labels[genre]`，缺则回退原始英文 |

> 必读条目**也**进各自类型分组——速览是全量目录，不是"必读之外"的补集（对齐 §5.6 模板）。`is_explore=True` 的条目在渲染时标 `🧭探索`。

### 5.4 数据概览（`build_overview(items, config) -> Overview`）

| 字段 | 怎么算 |
|---|---|
| `genre_distribution` | 按 `genre` 计数，键按 §6.1 genre 顺序排列（确定性） |
| `keywords` | 把所有条目的 `tags` 摊平，去掉 `#` 前缀后按出现频次降序、同频按首次出现序，取前 `top_keywords` 个 |

### 5.5 组装（`build_report(review_result, date_label, config) -> DailyReport`）

汇总 §5.2–5.4：

```
must_read   = select_must_read(reviewed_items, config)
categories  = group_by_category(reviewed_items, config)
overview    = build_overview(reviewed_items, config)
item_count  = len(reviewed_items)
explore_count = #{ it | it.is_explore }
is_pending  = review_result.is_pending
daily_take  = review_result.daily_take   # 可为 None
```

### 5.6 渲染（`render_markdown(report, config) -> str`）

按 PRD §5.6 模板拼字符串，分块、全确定性：

| 块 | 渲染要点 |
|---|---|
| 报头 | `# AI Daily · {date_label}`；`is_pending=True` 时紧接一行水印 `> {pending_watermark}` |
| 今日看点 | `daily_take` 非空才渲染 `> **今日看点**：…`；为 `None`/`""` 则整块省略 |
| 今日必读 | `## 🏆 今日必读`；每条编号，含 标题(中文+`title_en`) / 一句话(summary) / 解读 / 对你(takeaway) / 锐评(hot_take) / 评分+来源+时间 / 依据(evidence→`[claim](anchor)`)；`must_read` 为空则整块省略 |
| 分类速览 | `## 📚 分类速览`；按 `categories` 顺序，每组 `**{label}**` + 列表项 `` `[{score}]` `` (+`🧭探索`若 explore) + 标题 — 一句话 ｜ 链接 |
| 数据概览 | `## 📊 数据概览`；类型分布一行、高频关键词一行；`keywords` 空则省略关键词行 |
| 页脚 | 固定 `--- 📬 RSS ｜ 🗂 历史归档 ｜ 🏠 主站`（占位链接，本圈不接真站点） |

> 渲染**无随机、无 now()**，`date_label` 由参数注入 → 同输入同输出，snapshot 可锁定。

### 5.7 待审水印

| `review_result.is_pending` | 渲染 | `PublishResult.is_pending` |
|---|---|---|
| True | 报头打 `> {pending_watermark}` 水印，正文照常 | True |
| False | 无水印 | False |

本层只**标**，不拦发布；"未审自动发"拦不拦交 CLI / P1 渠道层。

## 6. 配置与加载

### 6.1 `config/publish.yaml`

```yaml
must_read_count: 3                       # 今日必读取前几条
top_keywords: 4                          # 数据概览高频关键词个数
pending_watermark: "⚠ 未审草稿（待人工定稿，勿直接发布）"
genre_labels:                             # genre → 中文展示名(也定义组间顺序)
  official: "官方"
  paper: "论文"
  model: "模型"
  tool: "工具 / 开源"
  news: "新闻"
  community: "社区"
  blog: "博客"
```

`PublishConfig`（dataclass，默认值同上）：`must_read_count` / `top_keywords` / `pending_watermark` / `genre_labels`。`genre_labels` 的**键顺序**即分类速览的组间顺序与数据概览的计数顺序。

加载器 `load_publish_config(path)` 与 `load_review_config` 同风格（缺文件 → 默认；`.get` per field）。

### 6.2 没有 LLM / prompts / 渠道凭证

本层**不引入** LLM、不读 prompts、不读任何渠道密钥（Notion token 等延后 P1）。展示常量全在 `config/publish.yaml`，运行时加载。

## 7. 错误与回退（都不致命）

| 情况 | 怎么处理 |
|---|---|
| 上游静默 / 0 条 | 空 `PublishResult`，`markdown=""`、`is_silent=True`，不抛 |
| `daily_take` 为 None / "" | 省略今日看点块，其余照常 |
| 没有合格必读条目 | 省略今日必读块，速览/概览照常 |
| 某条 `tags` 为空 | 不计入关键词；速览/必读照常渲染 |
| 某条 `evidence` 为空 | 必读块"依据"行省略（该条本不该进必读，但渲染不崩） |
| 配置文件缺/空 | `PublishConfig()` 默认 |
| `is_pending=True` | 照常渲染 + 报头水印；产物仍可手动发 |
| `--dry-run` | 跑 `collect→…→review→publish`，把 Markdown 打到 stdout |

## 8. 不变量（golden / snapshot 测试必须断言）

| # | 不变量 |
|---|---|
| 1 | 全量目录：`sum(len(c.items)) == item_count`，分类速览不漏不重 |
| 2 | 必读上限：`len(must_read) <= must_read_count`，且全部 `eligible_for_must_read==True` |
| 3 | 必读子集：`must_read` 的每条都出现在其 `genre` 对应的 `CategorySection` 里 |
| 4 | 类型分布守恒：`sum(genre_distribution.values()) == item_count` |
| 5 | 组间顺序：`categories` 按 `config.genre_labels` 键序，非空类目才出现 |
| 6 | 内容只读：渲染不改任何上游字段（`score`/`title`/`link`/… 恒等上游） |
| 7 | 水印：`is_pending=True` → Markdown 含 `pending_watermark`；False → 不含 |
| 8 | 看点可空：`daily_take` 为 None/"" → 输出不含今日看点块 |
| 9 | 必读为空：无合格条目 → 输出不含今日必读块 |
| 10 | 确定性：同 `ReviewResult` + 同 `date_label` → 同 `DailyReport` / 同 Markdown（含 snapshot 比对） |
| 11 | 空输入 → 空 `PublishResult`、`is_silent=True`、`markdown==""`、不抛 |
| 12 | 关键词：按频次降序取前 `top_keywords`，去 `#` 前缀，确定性 |

## 9. golden 用例（≥6，冻结 `ReviewResult` fixtures）

| # | 用例 | 断言要点 | 关联不变量 |
|---|---|---|---|
| 1 | 完整一份：多类型 + daily_take + 合格必读 | 四块齐全、Top3 准、分组顺序对、计数守恒 | 1,2,3,4,5 |
| 2 | 必读不足：仅 1 条合格 | `must_read` 1 条、不补位、速览仍全量 | 2 |
| 3 | 无合格必读：全不 eligible | 不渲染必读块、速览/概览照常 | 9 |
| 4 | 待审水印：`is_pending=True` | Markdown 含水印、`PublishResult.is_pending=True` | 7 |
| 5 | 看点为空：`daily_take=None` | 不渲染看点块 | 8 |
| 6 | explore 标记：含 `is_explore=True` 条目 | 速览该条标 `🧭探索`、`explore_count` 准 | 1 |
| 7 | 空输入 → silent | 空结果、`is_silent=True`、`markdown==""` | 11 |
| 8 | 确定性 + snapshot：固定 fixture | 两次调用全等；Markdown == 冻结快照 | 10 |
| 9 | 关键词聚合：tags 有重复 | 按频次降序取前 N、去 `#` | 12 |

## 10. 测试要求

| 类型 | 测什么 |
|---|---|
| contract | `load_publish_config`（缺文件回默认 / 覆盖字段）；`PublishConfig` / `DailyReport` / `CategorySection` / `Overview` / `PublishResult` schema；`--publish` CLI 形状 |
| golden | 冻结 `ReviewResult` fixtures 跑 §9 的 ≥6 用例，断言 §8 |
| snapshot | 一份固定输入的期望 Markdown 存 `tests/golden/data/`，比对 `render_markdown` 输出 |

- 全程**纯内存、不打网络、不调 LLM、不读真实渠道**（`ReviewResult` 直接构造注入）；时间用注入的 `date_label`，层内不取 now。
- 纯函数 `select_must_read` / `group_by_category` / `build_overview` / `build_report` / `render_markdown` 全离线可测。

## 11. 可观察（写 `runs`）

| 事件 | 何时 | 载荷 |
|---|---|---|
| `publish_start` | 进入 | `{run_id, input_count}` |
| `report_built` | 组装完 | `{must_read_count, category_count, item_count, is_pending}` |
| `publish_done` | 结束 | `{item_count, must_read_count, is_pending, silent}` |

## 12. 验收（对齐 PRD §2.1）

| 验收点 | 本层交付 |
|---|---|
| #7 发布成功率 / ≥1 渠道 | 渲染产物即"可发布稿"，Markdown 单渠道落地；多渠道推送 + 失败隔离延后 P1 |
| #5 解读零幻觉延续 | 必读只收 `eligible_for_must_read==True`；无证据条目进不了必读，渲染不编内容 |
| #1 端到端 | `collect→dedup→score→interpret→review→publish` 串得起来，`--dry-run --publish` 出 Markdown |
| #8 静默正确 | 上游静默时返回空结果、`markdown==""`，不抛 |
| 待审正确（§3.4） | `is_pending=True` 时打水印，供上层决定"未审自动发"拦不拦 |
| #9 可观察 | `publish_done` 等写入 `runs` |
