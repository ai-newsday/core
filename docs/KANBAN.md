# KANBAN — AI News Daily

> 唯一任务看板 + 进度表（合并自旧 `ROADMAP.md`）。源头意图见 `docs/intent/`，每层契约见 `docs/specs/`。
> 约定:一次一个子项目、小 PR、issue-per-PR、从真实 `origin/master` 起有意义分支名。
> 最后更新:2026-06-19。

---

## 1. 七层流水线 · 进度（MVP 闭环已完成）

| # | 层 | spec | 实现 | 测试 | 状态 |
|---|---|---|---|---|---|
| ① | 采集 collect | `specs/collection.md` | `pipeline/collect.py` + adapters | ✅ 绿 | ✅ 合并 master |
| ② | 去重聚类 dedup | `specs/dedup.md` | `pipeline/dedup.py` | ✅ 绿 | ✅ 合并 master |
| ③ | 打分配额 score | `specs/score.md` | `pipeline/score.py`（纯函数） | ✅ golden | ✅ 合并 master |
| ④ | 解读生成 interpret | `specs/interpret.md` | `pipeline/interpret.py`（LLM+回退） | ✅ golden | ✅ 合并 master |
| ⑤ | 审校 review | `specs/review.md` | `pipeline/review.py`（纯函数） | ✅ contract+golden | ✅ 合并 master |
| ⑥ | 发布 publish | `specs/publish.md` | `pipeline/publish.py`（纯函数渲染） | ✅ +snapshot | ✅ 合并 master |
| ⑦ | 反馈闭环 feedback | `specs/feedback.md` | `pipeline/feedback.py`（纯函数） | ✅ contract+golden | ✅ 合并 master |
| +0.5 | 质量自检 selfcheck | `specs/selfcheck.md` | `pipeline/selfcheck.py`（贴 flag 不 gate） | ✅ 绿 | ✅ 合并 (#14) |

后续增强(genre/publisher、信号源)见下方任务表。

---

## 2. 🔴 Blocked / 待决策

| ✓ | 任务 | 状态 | 详情 |
|---|---|---|---|
| ☐ | **Reddit 源生产被 IP 封死** | 🔴 待决策 | 2026-06-19 真实 cron(run `27805287782`)证实 `old.reddit.com/r/*` 在 GitHub Actions 出口 IP 整段 `403 Blocked`(反爬黑名单,非 UA)。本地能跑(51 条带 upvotes),**生产 yield=0**。#20 在生产里目前≈没做。方案:(a)加代理/换出口 IP;(b)换数据源(reddit OAuth/镜像/pushshift);(c)砍掉源回退到 enrich 贴 `hn_points`。**用户决定:先记下不停工,推进 GitHub。** |

---

## 3. 🚧 In Progress / 下一步（按优先级）

> 意图已确认:`docs/intent/telegram-feedback-loop-and-visibility.md`。产品形态=(b)对外,但现阶段先做到"我能看、能审、能定稿"。**这三件先于 GitHub 源。**

> **实现顺序(2026-06-20 重排):** 先把**实际坏掉的 Telegram 人审闭环解决并实测通**(M1),再回来做文风/渲染(M2)。文风+版式+配额规范已聊定并落 `references/editorial-and-format-sop.md`。

**M1 — 人审闭环可用 + 实测（先做）**

> 设计:`docs/superpowers/specs/2026-06-20-telegram-webhook-feedback-loop-design.md`。拆 3 个 plan。
> ⚠️ **合并约束:Plan 1/2/3 必须一起合并 master**。Plan 1 已让 collect 停止轮询;若单独合并,webhook 未激活前决策完全无人收集(比现状更差)。三者齐活、实测通过后再合。

| ✓ | plan | 任务 | 状态 |
|---|---|---|---|
| ☑ | **P1** | **Python 流水线**:决策适配器 + finalize 拉取并入(非致命) + collect 去轮询 + 终稿改简报+链接 + 卡片 4096 限长 + cli 接线 | ✅ 完成(commits `3145013`..`648d308`,全量 336 绿,双审+终审通过) |
| ☑ | **P2** | **CF Worker + KV**:webhook 端点(校验 secret → `answerCallbackQuery`+`editMessageText` → 写 KV → `GET /decisions`)+ register 脚本。`workers/telegram-webhook/` | ✅ **完成 + 实测通过**(2026-06-21)。已部署 `https://ai-newsday-telegram-webhook.ai-newwsday.workers.dev`;KV id `8e0df2cd19a04831b6f671ab378e03a6`。冒烟:点按钮秒回(toast+编辑消息)、`/decisions` 读出决策。⚠️ secrets(WEBHOOK_SECRET/DECISIONS_API_SECRET/bot token)已配在 Worker;**DECISIONS_API_SECRET 同值要在 Plan 3 配进 GitHub Actions**。 |
| ☐ | **P3** | **可见链 + 激活**:finalize.yml 换 PAT push 触发 `pages.yml`;`delivery.yaml` 切 `mode: webhook` + Worker URL;**删 dead `poll_decisions`/协议方法**(终审 Important #2);**端到端实测** | 待写 plan |

**M2 — 日报文风/版式/内容质量（后做,依据 SOP）**

| ✓ | 优先 | 任务 | 详情 |
|---|---|---|---|
| ☐ | **P1** | **S2 文风 prompts + 内容契约** | `interpret_item.md`/`daily_take.md` 产出新文风(钩子标题/成段正文/3 tags/英文术语);数据模型 `summary/takeaway/hot_take` → 新字段。依据 `references/editorial-and-format-sop.md`。 |
| ☐ | **P1** | **S3 publish 渲染重做** | 去 emoji、新页面结构(今日看点/必读成段/其余一行/去重/删数据概览/tags 展示)。确定性、snapshot 可测。与 S2 强耦合,可合一个 spec。 |
| ☐ | **P2** | **S4 配额 + 内容过滤(甲-3)** | 软配额+质量地板(目标10/上限11,每类门槛,见 SOP §4);垃圾空条目过滤、摘要不截病句、firehose 噪声降权。score/config + selfcheck。 |
| ☐ | **P2** | 子项目 2:`tool` genre + GitHub 源 | 最后。GitHub Trending/repos + schema 动 genre/publisher。 |

> 文风/版式/配额规范见 **`references/editorial-and-format-sop.md`**（v0.2,已锁定）。

---

## 4. 📋 Backlog

| ✓ | 任务 | 优先 | 详情 |
|---|---|---|---|
| ☐ | 一页多帖测试(Reddit adapter) | 低 | 给 `reddit.py` title-bounding(`things[i+1].start()`)补"一页两帖"解析测试;现只测过单帖页。独立小 PR。 |
| ☐ | 子项目 3:博客扩充 + validation 闸 | 中 | config + 探活脚本。当前 4 个 substack(gwern/garymarcus/lcamtuf/import-ai)生产 403,validation 闸正好把死源挡外或标 `manual`。 |
| ☐ | 子项目 4:每轮漏斗报告 | 中 | 落 run_dir 的 HTML/md,复用 `source_reports`+`0X_*.jsonl`+score `quota_applied`,几乎不加埋点。独立轻。 |
| ☐ | 子项目 5:跨轮看板 + 持久化 | 低 | 依赖 Hugo 站点部署(PR #5 建了 workflow,**尚未部署**)。 |
| ☐ | Issue #6:`--publish-only` no-op + draft 重发 | 低 | 小 bugfix。 |
| ☐ | 反馈→打分接线 | 中 | `quality_weight` 接回第 3 层评分;**先写 ADR** 说明信誉如何折进打分再动代码。 |
| ☐ | 多渠道发布(P1) | 中 | 复用 `DailyReport` 加 RSS/公众号/网站 JSON 渲染器 + 真实推送 + 失败隔离。**门槛:源质量达标后**。 |
| ☐ | 向量沉淀 / AI 编年史(P1) | 低 | Qdrant archive + 检索。长期资产。 |

> 子项目 2 开放设计点:新 `tool` genre 的 `genre_value` 权重 + 配额槽(总配额 8 是否调整/挤占);repo `publisher` 如何承载 org 身份(GitHub `owner.type` → company/individual)。

---

## 5. ✅ Done

| ✓ | 任务 | 详情 |
|---|---|---|
| ☑ | 子项目 1:HN + Reddit 信号源(#20) | 已合并。⚠️ Reddit 部分生产被封(见 §2);HN(Algolia front_page)待确认生产 yield。 |
| ☑ | genre/publisher split(#16) | `source_type` → `genre`+`publisher`+signal 层。ADR 0003。 |
| ☑ | 质量自检层 selfcheck(#14) | pipeline step 4.5,贴 `quality_flags` 不 gate。 |
| ☑ | feedback loop v1(#8) | 持久化 `feedback_events`/`quality_weights`,`quality_weight` 作机构影响力乘子。ADR 0002。 |
| ☑ | 早期增强(#4/#5/#10/#12) | recency/topic_boost、Hugo workflow、hf-papers daily、同源惩罚 tie-break。 |
| ☑ | 七层 MVP 闭环(Circle 1–7) | collect→dedup→score→interpret→review→publish→feedback 全合并、`--dry-run` 串得起来。 |

---

## 6. 每圈开发范式（superpowers 链）

`brainstorming`(spec) → `writing-plans`(计划) → `test-driven-development`(red→green) → `requesting-code-review`(contract+golden 全绿) → `finishing-a-development-branch`(合并+更新本看板)。

纪律(CLAUDE.md):一次只做一层不横跨;没有失败测试不写实现;对外副作用必须 `--dry-run`。

---

## 7. 文档地图

| 文档 | 作用 |
|---|---|
| `docs/PRD.md` / `docs/BRD.md` | 产品需求(V3.0.0)/业务背景 |
| `docs/specs/<层>.md` | 每层契约(接口/数据/算法/不变量/golden) |
| `docs/intent/*.md` | interview-me 确认的意图 |
| `docs/adr/*.md` | 架构决策记录 |
| `docs/superpowers/plans/*.md` | 每层逐任务 TDD 计划 |
| `docs/KANBAN.md` | **本文** — 唯一任务看板 + 进度表 |
