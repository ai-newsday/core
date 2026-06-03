# ROADMAP — 开发进度与文档地图

> 本文是项目的**可视化进度看板** + **文档导航** + **每圈开发范式**。
> 每完成一个 Circle 更新此文。最后更新：2026-06-03（Circle 7 feedback 已合并 — 七层闭环完成）。

---

## 1. 七层流水线 · 全景

```mermaid
flowchart LR
    C1["① 采集<br/>collect()"]:::done
    C2["② 去重聚类<br/>dedup()"]:::done
    C3["③ 打分配额<br/>score()"]:::done
    C4["④ 解读生成<br/>interpret()"]:::done
    C5["⑤ 审校<br/>review()"]:::done
    C6["⑥ 发布<br/>publish()"]:::done
    C7["⑦ 反馈闭环<br/>feedback()"]:::done

    C1 --> C2 --> C3 --> C4 --> C5 --> C6 --> C7
    C7 -.读者画像/相关度.-> C3

    classDef done fill:#1f7a1f,stroke:#0d3d0d,color:#fff;
    classDef spec fill:#b58900,stroke:#6b4f00,color:#fff;
    classDef todo fill:#3a3a3a,stroke:#1a1a1a,color:#bbb;
```

图例：🟩 已实现并合并 · 🟨 已写 spec / 进行中 · ⬜ 待开始

---

## 2. 进度表

| # | 层 | spec | 实现 | 测试 | dry-run | 状态 |
|---|---|---|---|---|---|---|
| ① | 采集 collect | `specs/collection.md` | ✅ `pipeline/collect.py` + 3 adapters | ✅ 26 绿 | ✅ 28 源实跑 | **🟩 已合并 (master)** |
| ② | 去重聚类 dedup | `specs/dedup.md` | ✅ `pipeline/dedup.py` + embedding/vectorstore adapters | ✅ 34 绿 | ✅ `--dry-run --dedup` 实跑 | **🟩 已合并 (master)** |
| ③ | 打分配额 score | `specs/score.md` | ✅ `pipeline/score.py`（纯打分+配额） | ✅ golden | ✅ `--dry-run --score` 实跑 | **🟩 已合并 (master)** |
| ④ | 解读生成 interpret | `specs/interpret.md` | ✅ `pipeline/interpret.py`（LLM 解读+抽取式回退） | ✅ golden | ✅ `--dry-run --interpret` 实跑 | **🟩 已合并 (master)** |
| ⑤ | 审校 review | `specs/review.md` | ✅ `pipeline/review.py`（纯函数留/删/改/排序+必读门重算） | ✅ contract+golden | ✅ `--dry-run --review` 实跑 | **🟩 已合并 (master)** |
| ⑥ | 发布 publish | `specs/publish.md` | ✅ `pipeline/publish.py`（纯函数 DailyReport 组装 + Markdown 渲染） | ✅ contract+golden+snapshot | ✅ `--dry-run --publish` 实跑 | **🟩 已合并 (master)** |
| ⑦ | 反馈闭环 feedback | `specs/feedback.md` | ✅ `pipeline/feedback.py`（纯函数派生事件+按源聚合+增量算权重） | ✅ contract+golden | ✅ `--dry-run --feedback` 实跑 | **🟩 已合并 (master)** |

---

## 3. 每圈开发范式（superpowers 链）

> 对应你提的"每次迭代的开发范式 skill"。本项目每个 Circle **固定走这 5 步**，每步对应一个 superpowers skill。

```mermaid
flowchart TD
    A["① brainstorming<br/>聊清需求/多解读/锁决策"]:::s --> B["② writing-plans<br/>逐任务 TDD 计划"]:::s
    B --> C["③ test-driven-development<br/>red → green → commit/任务"]:::s
    C --> D["④ requesting-code-review<br/>contract+golden 全绿后自审"]:::s
    D --> E["⑤ finishing-a-development-branch<br/>合并 master，更新本 ROADMAP"]:::s
    classDef s fill:#264f78,stroke:#13294a,color:#fff;
```

| 步 | skill | 产物 | 门槛 |
|---|---|---|---|
| ① 想清 | `superpowers:brainstorming` | spec (`docs/specs/<层>.md`) | 你确认设计 |
| ② 定计划 | `superpowers:writing-plans` | 计划 (`docs/superpowers/plans/<日期>-<层>.md`) | 逐任务可验收 |
| ③ 实现 | `superpowers:test-driven-development` | 代码 + 测试 | 先写失败测试再写实现 |
| ④ 审查 | `superpowers:requesting-code-review` | 评审意见 | contract+golden 全绿 |
| ⑤ 收尾 | `superpowers:finishing-a-development-branch` | 合并 + 进度更新 | 验收对齐 PRD §2.1 |

> 纪律（CLAUDE.md）：一次只做一层、不横跨；没有失败测试不写实现；对外副作用必须支持 `--dry-run`。

---

## 4. 文档地图

```mermaid
flowchart TD
    PRD["docs/PRD.md<br/>要什么 (V3.0.0)"] --> SPECS
    BRD["docs/BRD.md<br/>业务背景"] --> PRD
    SPECS["docs/specs/*.md<br/>每层契约"]
    SPECS --> S1["collection.md ✅"]
    SPECS --> S2["dedup.md ✅"]
    SPECS --> S3["score.md ✅"]
    SPECS --> S4["interpret.md ✅"]
    SPECS --> S5["review.md ✅"]
    SPECS --> S6["publish.md ✅"]
    SPECS --> S7["feedback.md ✅"]
    PLANS["docs/superpowers/plans/*.md<br/>每层 TDD 计划"]
    PLANS --> P1["2026-05-31-collection-layer.md ✅"]
    PLANS --> P2["2026-05-31-dedup-layer.md ✅"]
    PLANS --> P3["2026-05-31-score-layer.md ✅"]
    PLANS --> P4["2026-06-01-interpret-layer.md ✅"]
    PLANS --> P5["2026-06-02-review-layer.md ✅"]
    PLANS --> P6["2026-06-02-publish-layer.md ✅"]
    PLANS --> P7["2026-06-03-feedback-layer.md ✅"]
    REF["references/ + src/prompts/<br/>产品 SOP / 内容判断"]
    RM["docs/ROADMAP.md<br/>← 你在这里"]
    SB["docs/Session启动包.md<br/>每圈启动手册"]
```

| 文档 | 作用 |
|---|---|
| `docs/PRD.md` | 产品需求（V3.0.0），每层验收标准源头 |
| `docs/BRD.md` | 业务背景 |
| `docs/specs/<层>.md` | 每层契约（接口/数据/算法/不变量/golden 用例） |
| `docs/superpowers/plans/<日期>-<层>.md` | 每层逐任务 TDD 计划 |
| `docs/Session启动包.md` | 每圈开发启动手册（范式见本文 §3） |
| `docs/ROADMAP.md` | **本文** — 进度看板 + 文档导航 |

---

## 5. 下一步（MVP 七层闭环已完成 → P1）

七层 MVP（collect→dedup→score→interpret→review→publish→feedback）已全部合并 master、`--dry-run` 串得起来。接下来是 P1 增强，每项仍走本文 §3 的 5 步范式、独立小 PR：

1. **反馈 → 打分接线**：把 `feedback` 产出的 `quality_weight` 接回第 3 层评分（改 `scoring.py`，**先写 `docs/adr/` 决策记录**说明信誉如何折进打分，再动代码）。
2. **多渠道发布**：复用 `DailyReport` 加 Notion / 公众号 HTML / 网站 JSON / RSS 渲染器 + 真实推送 + 失败隔离（渠道 adapter + mock API）。
3. **更多反馈信号**：阅读行为（open/dwell/forward）、显式 👍👎、`reader_relevance` 重算（依赖前端埋点/渠道回传）。
4. **持久化落地**：JSON 账本 → SQLite `feedback` 表 + 正反馈向量进 Qdrant（替掉当前的内存/JSON 占位）。
5. **采集层遗留**（见下方 backlog）。

### 已完成（Circle 7 · feedback）
- `feedback()` 把人工审阅动作（留/删/改）回收成按源信誉信号并增量更新 `quality_weight`：`derive_events` 从**进审阅前**全量条目派生事件（被删条目也产 `drop`，负反馈不漏）、`aggregate_by_source` 按源聚合（字母序确定性）、`compute_quality_weights` 增量算权重（留升/删降/改记半正、样本下限保护、夹界 `[0.5,1.5]`）；**纯函数、不调 LLM、不打网络、不落盘**。
- 信号只收 `review_action`（PRD §4.5 信号①最强隐式）；②阅读行为 ③👍👎 与 `reader_relevance` 延后 P1。JSON 事件账本（`load_feedback_events`/`load_quality_weights` 只读）替代 SQLite/Qdrant；产 `weight_diff`（旧→新）可解释。
- **只算不接打分**——`quality_weight` 进第 3 层评分是显式未来改动（配 ADR）。contract+golden（§9 十用例：派生含 drop / 聚合 / 全留升 / 全删降 / 夹界 / 样本不足 / 历史保留 / edit 弱正 / 空静默 / 确定性）全绿；`--dry-run --feedback` 链路实跑（dry-run 只打印权重+差异，不落盘）。全套 210 测试绿。

### 已完成（Circle 6 · publish）
- `publish()` 把审阅定稿 `ReviewResult` 两步组装：`build_report` 产统一内容模型 `DailyReport`（今日看点/必读 Top3/分类速览/数据概览），`render_markdown` 渲染成 Markdown；**纯核心、不调 LLM、不打网络、无渠道副作用**。
- 必读 Top3 只收 `eligible_for_must_read==True`（无证据/回退条目天然排除，延续 PRD §4.4 零幻觉）；分类速览按 `type_labels` 键序分组、全量目录守恒；数据概览聚合 tags 取高频关键词。
- `is_pending=True` 时报头打"未审草稿"水印但照常渲染（发不发交上层，PRD §3.4）；空输入静默。contract+golden（§9 九用例）+ Markdown snapshot 全绿；`--dry-run --publish` 链路实跑。多渠道渲染器（Notion/公众号/JSON/RSS）+ 真实推送延后 P1。

### 已完成（Circle 5 · review）
- `review()` 纯函数应用人工"留/删/改/排序"决策（按 `link` 索引的 `ReviewDecision`），产 `ReviewedItem`；**不调 LLM、不打网络**，唯一 IO 是读决策 JSON。
- edit 只改内容字段（出处只读），改后重夹 title/summary + 过滤非法证据锚点 + 重算必读门；`interpretation_status` 只读，回退条目洗不白；无决策→`is_pending=True`（待审不自动发，PRD §3.4）。
- 审阅动作（`review_action` + `edited_fields`）回收为反馈信号供 Circle 7；contract+golden（§9 九用例）全绿；`--dry-run --review` 链路实跑。

### 已完成（Circle 4 · interpret）
- `interpret()` 逐条 LLM 解读（结构化 JSON + schema 校验），任一失败→抽取式回退、零编造；`LLMProvider` 协议 + `OpenAICompatLLM`(ModelScope) 适配器 + `FakeLLMProvider` 注入测试。
- 证据链锚点必须 ∈ link∪related_links，非法锚点丢弃；`eligible_for_must_read` 实现「无证据不进必读」；一次日报级「今日看点」。
- 验收门 PRD #5 解读零幻觉（golden 断言回退零编造、必读门）；`--dry-run --interpret` 链路实跑；偏离记于 `docs/adr/0001-llm-openai-compatible.md`。

### 已完成（Circle 3 · score）
- `compute_scores()` / `apply_quota()` 纯函数（多维 breakdown 9 键，registry 优先级折进"机构影响力"；类型配额严格按类型不跨类型补位）+ `score()` orchestrator（emit score 事件）。
- 权重/配额全读 `config/scoring.yaml`（recency/penalty 拍平），不写死；冻结 fixtures 驱动 6 个 golden 用例（配额裁剪/未满全留/时效档/同源惩罚/空输入静默/clamp+breakdown 求和+确定性）。
- 验收门 PRD #4 配额生效 100% 通过；`--dry-run --score`（collect→dedup→score）链路实跑。

### 已完成（Circle 2 · dedup）
- `cluster()` 纯函数（贪心阈值聚类，registry 优先级注入）+ `EmbeddingProvider`(ModelScope)/`VectorStore`(InMemory，Qdrant 后置) 适配器。
- `FakeEmbeddingProvider` 冻结向量驱动 6 个 golden 用例；embedding 失败降级为全单例（spec §7）。
- 验收门 PRD #3 去重覆盖率 100% 通过；`--dry-run --dedup` 链路实跑。

### 待办 backlog（采集层遗留，不阻塞 Circle 4）
- 修死链：`microsoft-ai` (403)、`meta-ai` (404) feed URL 过期。
- `hf-models` firehose 噪声大（topic-agnostic，过滤是 Circle 3 的职责）。
