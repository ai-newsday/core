# 设计:HN + Reddit 信号源(子项目 1)

- 日期:2026-06-18
- 状态:待评审
- 上游意图:`docs/intent/source-expansion-and-observability.md`(① 增信号源)
- 关联:`docs/adr/0003-genre-publisher-split.md`(genre/publisher 模型)、现有 `enrich` 层(HN by-URL 富集)

## 背景与目标

源质量诊断(2026-06-17)确认:除 `hf-papers` 外几乎没源带信号,日报有效条目太少。本子项目新增 **Hacker News 和 Reddit 两个独立 source adapter**,把"人群高赞内容"作为**自带信号的候选条目**引入(option A:带进新条目,不只给已采条目打分)。

**目标**:HN 首页高分帖、Reddit 各 AI 子版高赞帖成为候选 item,携带 `points`/`upvotes` 信号,凭信号在打分层自然上榜。

**非目标(本子项目不做)**:GitHub Trending / `tool` genre(子项目 2)、博客扩充(子项目 3)、可观测看板(子项目 4/5)、阈值精校(待 ④看板数据后调,本期只给 config 占位默认)、Reddit OAuth(先用公开 `.json`,被限流再说)。

## 核心决策(brainstorm 结论)

1. **option A:聚合器带进新条目**,与现有 `enrich` 层(by-URL 贴 `hn_points`)并存、互补。
2. **`link` 指向**:有外链 → 底层原文 URL;无外链 / Reddit 自帖本身是文章 → 帖子 permalink。
3. **去重/富集分工(dedup 与 enrich 一行不改)**:
   - HN/Reddit 的独有价值 = 发现我们没追的来源的好内容(singleton,自带信号,正常入池)。
   - 撞车(同文已被直接采到)→ dedup 按现有规则合并;被丢的聚合器副本信号丢了无妨,因 `enrich` 层会按 URL 给存活的 tracked 条目贴 `hn_points`。
   - 聚合器条目自带 popularity → `enrich._has_popularity` 自动跳过,不重复查。
4. **genre/publisher**:HN 帖、Reddit 帖一律 `(writeup, individual)`。
5. **相关性过滤**:HN 是泛科技首页 → **AI 关键词闸(标题/URL 命中)+ 最低 points 阈值**双闸;Reddit 订的就是 AI 子版 → **只用最低 upvotes 阈值,无关键词闸**。
6. **时间**:中央 `window_hours` 窗口照旧;Reddit 用 `t=day`;每条解析出 `published_at` 让窗口过滤生效。
7. **阈值/关键词全 config 驱动**(per-source),不硬编;默认占位,待看板校准。

## Schema 改动(`src/core/types.py`)

`SourceSpec`:
- `adapter` Literal 增加 `"hn"`、`"reddit"` → `Literal["rss", "hf_papers", "hf_models", "hn", "reddit"]`。
- 新增两个可选字段:
  ```python
  min_score: int | None = None        # HN points / Reddit ups 下限; None = 不过滤
  keywords: list[str] | None = None   # HN AI 关键词表(标题/URL 命中); Reddit 不填
  ```
- 其余字段不变。`genre`/`publisher` 复用现有(填 `writeup`/`individual`)。

## HN adapter(`src/adapters/sources/hn.py`)

- 数据源:HN Algolia(`https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=N`),复用现有 `HNAlgoliaClient` 同款 httpx 调用风格(本 adapter 自带请求,不依赖 enrich 的 client 实例)。
- 每条 hit 取:`title`、`url`、`points`、`num_comments`、`created_at_i`(epoch)、`objectID`。
- **过滤**(顺序):
  1. `points >= source.min_score`(`min_score` 为 None 时跳过该闸)。
  2. `source.keywords` 任一(小写)命中 `title` 或 `url` —— 命中才留(`keywords` 为 None/空时不过滤,但 HN 行应配 keywords)。
- `link`:`url` 存在 → `url`;否则(Ask/Show 自帖)→ `https://news.ycombinator.com/item?id=<objectID>`。
- 产 `RawItem(title_en=title, link=…, source=source.name, genre=source.genre, publisher=source.publisher, published_at=<created_at_i→tz-aware UTC>, signals={"points":…, "num_comments":…})`,`signals` 去空。
- 容错:HTTP / JSON 错误 → 抛(collect 的 `_run_one` 捕获记 `failed`,单源不挂全局)。

## Reddit adapter(`src/adapters/sources/reddit.py`)

- 数据源:`https://www.reddit.com/r/<sub>/top.json?t=day&limit=N`(`url` 字段直接填该端点;`<sub>` 隐含在 url 里)。
- 请求头:描述性 `User-Agent`(如 `"ai-newsday/1.0 (by /u/...)"`;Reddit 对通用 UA 直接 429)。
- 解析 `data.children[].data`:`title`、`url`(外链)、`permalink`、`is_self`、`ups`、`num_comments`、`created_utc`、`selftext`。
- **过滤**:`ups >= source.min_score`(无关键词闸)。
- `link`:`is_self`(自帖)→ `https://www.reddit.com<permalink>`;否则 → `url`(外链)。
- `raw_summary`:自帖取 `selftext`(截断),外链留空。
- 产 `RawItem(… genre=writeup, publisher=individual, published_at=<created_utc→tz-aware UTC>, signals={"upvotes": ups, "num_comments":…})`。
- 容错:429/HTTP/JSON 错误 → 抛 → collect 记 `failed`(单源隔离)。OAuth 留作后续回退。

## 注册(`src/adapters/sources/__init__.py`)

```python
ADAPTERS = {
    "rss": RSSAdapter(), "hf_papers": HFPapersAdapter(), "hf_models": HFModelsAdapter(),
    "hn": HNAdapter(), "reddit": RedditAdapter(),
}
```

## `config/sources.yaml` 新增行(均 `genre: writeup, publisher: individual`)

- HN ×1:`{name: hackernews, url: "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=50", adapter: hn, status: working, priority: 2, min_score: 100, keywords: [AI, LLM, GPT, model, agent, diffusion, neural, transformer, ...], max_items: 15}`
- Reddit ×8(`adapter: reddit`,`max_items: 10`):
  - 产品:`r/OpenAI`、`r/ClaudeAI`、`r/StableDiffusion`、`r/GeminiAI`、`r/midjourney`、`r/comfyui`
  - 社区:`r/LocalLLaMA`、`r/MachineLearning`
  - 例:`{name: reddit-localllama, url: "https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=25", adapter: reddit, genre: writeup, publisher: individual, status: working, priority: 3, min_score: 50, max_items: 10}`
- **阈值为保守起步值**(HN `points≥100`、Reddit `ups≥50`),宁缺毋滥;上线后用 ④看板看各源真实分布再校准(大版如 r/MachineLearning 可上调,小版如 r/comfyui 可下调)。

## 错误处理 / 边界

- 单源失败非致命(collect `_run_one` 已有 try/except + `SourceReport(status=failed)`)。
- `published_at` 必须 tz-aware(epoch→UTC);解析失败的条目丢弃(同 rss adapter "drop undated")。
- `min_score=None` / `keywords=None` → 该闸不生效(向后兼容、防御性)。
- 不改 dedup / enrich / score / types 的打分逻辑(仅 SourceSpec 加字段 + 注册)。

## 测试策略(TDD,先红后绿)

1. **契约 `test_hn_adapter.py`**(mock Algolia JSON):字段映射、`points` 阈值闸、关键词闸(命中/未命中)、`link` 外链 vs 自帖回退、`signals` 内容、空结果、HTTP 错误抛出。
2. **契约 `test_reddit_adapter.py`**(mock `.json`):字段映射、`ups` 阈值闸、`is_self` → permalink、外链 → url、`created_utc` 解析、UA 头存在、429/错误抛出。
3. **`test_adapter_registry.py`**:`ADAPTERS` 含 `hn`/`reddit`。
4. **`test_types.py`**:`SourceSpec` 接受 `adapter="hn"/"reddit"` + `min_score`/`keywords`;旧行无新字段仍合法。
5. **`test_sources_yaml.py`**:全量 spec 校验(新增 9 行能过 `SourceSpec`),无重复 URL。
6. **集成**:`--dry-run`(collect-only)能跑到这两类源、产 `source_reports`;真实联网验证留人工(thresholds 待校准)。

CLAUDE.md 约束:contract/golden CI 全绿才合;对外副作用支持 `--dry-run`;阈值/关键词读 config 不写死。

## 实施顺序(给 writing-plans 的提示)

1. `types.py`:`SourceSpec.adapter` 加两值 + `min_score`/`keywords` 字段(+ test_types)。
2. HN adapter + 契约测试。
3. Reddit adapter + 契约测试。
4. 注册 ADAPTERS + registry 测试。
5. `sources.yaml` 新增 9 行 + sources_yaml 测试。
6. `--dry-run` 集成冒烟 + 真实联网抽查(确认能拉到、过滤生效)。
7. spec/docs 收尾。

## 风险

- Reddit `.json` 反爬:即便带 UA 也可能 429 → 该源记 `failed`、不挂全局;真限流再上 OAuth(后续)。
- HN/Reddit 与直接采源重叠 → 由 dedup 合并(已分析,信号靠 enrich 不丢)。
- 阈值占位若过松 → 重新引入噪音;靠 ④看板尽快校准(本期默认偏保守)。
