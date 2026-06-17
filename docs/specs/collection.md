# Spec — 采集层 (Collection)

> 放置路径：`docs/specs/collection.md`。这是七层流水线的第 1 层，也是 MVP 第一个要实现的模块。
> 对应 PRD §3、§4.1、§2.1（验收 #1/#2/#8）。

## 1. 目的

从多个源**并行采集**近期条目，归一化为统一的 `RawItem`，**任一单源失败都不阻断全链**。这一层只负责"拿到干净、去噪前的条目 + 记录每个源的健康状态"，不做去重/打分/解读。

直接服务的痛点：fetcher 经常坏 → 时效性/多样性差。本层的成败标准是**"偶尔少一条"，而不是"整条链崩"**。

## 2. 范围 / 非目标

- **做**：加载源注册表、并行抓取、解析归一化、时间窗过滤、难抓源兜底、记录源健康。
- **不做**：去重聚类（第 2 层）、打分（第 3 层）、翻译/摘要/解读（第 4 层）。本层不调用 LLM。

## 3. 接口契约

```python
def collect(config: CollectionConfig, run_ctx: RunContext) -> CollectionResult: ...
```

- **输入 `CollectionConfig`**：
  - `sources_registry_path: str` —— 源注册表文件路径（见 §6）
  - `window_hours: int = 24` —— 时间窗（默认 24h，最多 36h）
  - `max_window_hours: int = 36`
  - `concurrency: int = 10` —— 并发抓取数
  - `timeout_s: int = 15` —— 单源超时
  - `firecrawl_enabled: bool = false` —— 难抓源是否走 Firecrawl 兜底
- **输入 `RunContext`**：`run_id`、`now`（注入便于测试）、`logger`。
- **输出 `CollectionResult`**：
  - `items: list[RawItem]` —— 去噪前、未去重的归一化条目
  - `source_reports: list[SourceReport]` —— 每个源的成功/失败/条目数/错误码
  - `is_silent: bool` —— 时间窗内无任何条目时为 `True`

## 4. 数据契约

```python
class RawItem:           # 本层产物(NewsItem 的前身, 不含 score/summary/tags 等下游字段)
    title_en: str        # 必填(原始标题; 中文源则同填 title_en 占位)
    link: str            # 必填, 唯一性后续去重用
    source: str          # 必填, 源标识
    genre: str        # paper|model|announcement|writeup|news
    publisher: str    # lab|company|individual|media
    published_at: datetime  # 必填, 带时区
    raw_summary: str | None # 源自带摘要(若有), 不强制
    image_url: str | None
    fetched_via: str     # "native" | "firecrawl"  (可观察)

class SourceReport:
    name: str
    status: str          # "working" | "failed" | "empty"
    item_count: int
    error: str | None
    elapsed_ms: int
```

## 5. 错误与回退

| 情况 | 处理 |
| --- | --- |
| 单源 403/404/超时 | 标 `failed`，记 `error`，**继续其他源（非致命）** |
| 单源返回 0 条（窗口内） | 标 `empty`，不算错误 |
| 难抓源（JS 渲染/反爬）且 `firecrawl_enabled` | 用 Firecrawl 兜底，`fetched_via="firecrawl"`；仍失败则标 `failed` |
| 源注册表加载失败 | 回退到硬编码备选源清单，并在 run 日志告警 |
| **所有源都失败或为空** | `items=[]`, `is_silent=True`，**不抛异常、不产生空数据**（下游据此走 `[SILENT]`） |

## 6. 配置：源注册表（`references/sources-registry.md` 或 `config/sources.yaml`）

```yaml
- name: hf-papers
  url: https://huggingface.co/api/papers
  type: paper
  status: working        # working|manual|failed
  priority: 1            # 1 最高
  needs_firecrawl: false
- name: openai-blog
  url: https://openai.com/news/rss.xml
  type: official
  priority: 2
# RSSHub 失效迁移映射保留在此(原生 RSS 优先)
```

## 7. 不变量（golden 测试必须断言）

1. 输出中**没有任何** `published_at` 早于 `now - max_window_hours` 的条目。
2. 每个 `RawItem` 的 `title_en / link / source / genre / publisher / published_at` 均非空。
3. 任一源抛错**不会**使 `collect()` 抛异常（失败被收进 `source_reports`）。
4. `source_reports` 覆盖注册表里**每一个**启用源（成功/失败/空都要有记录）。
5. 所有源失败/空 ⇒ `is_silent=True` 且 `items==[]`。
6. `fetched_via` 仅为 `"native"` 或 `"firecrawl"`。

## 8. golden 用例（fixtures 驱动，≥4）

1. **混合源含一个 403**：3 源正常 + 1 源 403 → 返回 3 源条目，第 4 源 `status="failed"`，不抛异常。
2. **全部窗口外**：所有源都有数据但都早于窗口 → `items==[]`，`is_silent=True`。
3. **跨源重复**：两源给出同一事件 → **本层保留为两条**（去重是第 2 层的事），断言不在采集层误删。
4. **难抓源**：标记 `needs_firecrawl` 的源，`firecrawl_enabled=true` 时 `fetched_via="firecrawl"`；`false` 时跳过并 `failed`。
5. **注册表缺失**：加载失败 → 用备选源 + 告警，仍能产出条目。

## 9. 测试要求

- **contract**：每个源 adapter 在 mock 响应下返回合法 `RawItem[]`（schema 校验）。
- **golden**：用 `fixtures/sources/*`（冻结的真实响应样本）驱动上面 5 个用例，断言 §7 不变量。
- 时间相关一律用注入的 `run_ctx.now`，**不依赖真实当前时间**（保证确定性）。

## 10. 可观察

每个源产生 `source_fetch_success{name,item_count}` 或 `source_fetch_fail{name,error_code}` 事件，写入 `runs`；`collect()` 结束写 `collection_done{total_items, silent}`。

## 11. 验收（对齐 PRD §2.1）

- #1 端到端可跑：`collect()` 能从注册表走到 `CollectionResult`，无人工干预。
- #2 采集鲁棒：单源失败不阻断；≥10 源稳定可用；失败有记录。
- #8 静默正确：无合格条目时 `is_silent=True`，不产空数据。
