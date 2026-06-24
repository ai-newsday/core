# KANBAN — AI News Daily

> 唯一任务看板 + 进度表（合并自旧 `ROADMAP.md`）。源头意图见 `docs/intent/`，每层契约见 `docs/specs/`。
> 约定:一次一个子项目、小 PR、issue-per-PR、从真实 `origin/master` 起有意义分支名。
> 最后更新:2026-06-23。

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

## 3. 🚧 下一步（按优先级）

> **M1(人审闭环+可见链)与 M2(文风/版式/配额/过滤)已全部 SHIPPED**(见 §5)。pipeline 上线,每日 **09:00 北京(01:00 UTC `finalize.yml`)** 自动出报,Pages 部署,live **https://ai-newsday.github.io/core/**。

> **竞品/BRD 分析见 `docs/competitive-analysis-ai-news.md`**(2026-06-24,12 个开源 AI 日报项目实读对比)。以下 P0 直接来自该分析的结论。

| ✓ | 优先 | 任务 | 详情 |
|---|---|---|---|
| ☐ | **P0** | **放宽发卡池(解耦 可审候选 vs 发布 top-N)** | **根因**:`--tick collect` 在发卡前就 `score→quota` 砍到 top-11(`interpret(sres.selected_items)`),Telegram 只发这 11 条 → 低信号但重要的发布(如 **Krea-2**,来自 comfy priority-3 writeup 零信号)**到不了人眼前,无从 keep**。修:发卡阶段放宽(发 relevant 的 top-N 更大,或不在发卡前砍配额),最终刊物仍由 `total_limit` 控。**最高优先,直接解"只发 11 条没得选"。** |
| ☐ | **P0** | **Reddit 换 PRAW 官方 OAuth** | 现 old.reddit HTML 抓取在 prod 403、yield=0。竞品 `reddit-ai-trends` 用 `praw`(client_id/secret)走官方 API**不吃 403**。换之,拿回 Reddit 信号。**现成解。**(替代旧 §2 的"代理/换源/砍"三选一。) |
| ☐ | **P1** | **评估 Folo cookie 读 X(首发信道)** | 我们零 X 覆盖=最大缺口(Krea-2 等先发 X)。竞品 `CloudFlare-AI-Insight` 用 Folo cookie 读 X。**Folo RSS 阅读免费+开源(AGPL)**,只 AI 功能付费 → 免费层可跑。备:tuber0613 那份 AI 大佬 X handle 清单可复用。**不碰付费 X API;不自托管 RSSHub-X(脆+封号)。** |
| ☐ | **P1** | 故事线合并(同事件跨源聚合) | 竞品 `ai-news-radar` 把同一事件多源报道聚成时间线;我们仅按 genre 分类。 |
| ☐ | **P2** | 多频率 + 差异化输出 | 4H/周/月(clawfeed)、播客 TTS(CloudFlare-AI)、社媒卡图(ai-daily-skill)。多渠道发布(RSS/公众号/JSON)归此。 |
| ☐ | **P2** | 可选:per-genre 质量地板 | 仅当 flat-60 `min_display_score` floor 误判某 genre 时再做。 |

> 文风/版式/配额规范见 **`references/editorial-and-format-sop.md`**(v0.2,已锁定);标杆=TLDR AI / The Rundown / Ben's Bites / Import AI(SOP §7)。

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
| ☑ | 子项目 2:GitHub 源(releases+trending)(#36) | `github_releases`(comfyui/ollama/vllm)+`github_trending`(Search 保底+Trending 尽力)+`github_stars` 信号轴(ADR 0003 一致,不造 tool genre)。[PR #37](https://github.com/ai-newsday/core/pull/37)。**trending 出老 repo 修复**:注入 `created:>=now-180d` 只捞新建([PR #43](https://github.com/ai-newsday/core/pull/43),#42)。 |
| ☑ | finalize 确认门(#38) | 未确认内容不进报告:`select_report_items` 只放显式 keep/edit。[PR #39](https://github.com/ai-newsday/core/pull/39)。 |
| ☑ | finalize 跨天去重(#44) | `published_items` 表排除已在别 date_label 发过的条目(72h 窗口跨天复发)。[PR #45](https://github.com/ai-newsday/core/pull/45)。 |
| ☑ | 扩源(#40) | 22 个新源:聚合 newsletter(smol/LWiAI/gradient)+公司官博(Google/cursor/windsurf)+产品 YouTube 第一方(luma/runway/kling…)+OSS releases(sglang/unsloth)。[PR #41](https://github.com/ai-newsday/core/pull/41)。+竞品补 MarkTechPost/Wired-AI/Meta-Research。可达性见 [[ai-source-reachability]]。 |
| ☑ | state.db 移出 git(#25,ADR 0004) | 去 `!data/state.db` 白名单,`git rm --cached`,改用 `actions/cache`(rolling key)跨 run 持久化;`content/` 仍进 git。[PR #34](https://github.com/ai-newsday/core/pull/34)。 |
| ☑ | M2 文风/版式/内容质量 | M2-A voice/render `summary/takeaway/hot_take`→`body`、去 emoji 分类渲染(#27);M2-B1 AI 相关性过滤+词界匹配(#29);M2-B2 firehose 降权+配额 8→11(#31);report-yesterday 晨报汇总昨天完整一天(#33)。SOP `references/editorial-and-format-sop.md`。 |
| ☑ | M1 Telegram 人审闭环 + 可见链 | CF Worker+KV webhook(点按钮秒回→写 KV→finalize 拉取),finalize 日 cron 用 PAT 触发 Pages(#21/#24)。上线自动出报。 |
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
