# KANBAN — AI News Daily

> 唯一任务看板 + 进度表（合并自旧 `ROADMAP.md`）。源头意图见 `docs/intent/`，每层契约见 `docs/specs/`。
> 约定:一次一个子项目、小 PR、issue-per-PR、从真实 `origin/master` 起有意义分支名。
> 最后更新:2026-07-25。

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

（暂无。Reddit 生产 403 已由 #55 换 `.rss` 端点解决,见 §5。）

---

## 3. 🚧 下一步（按优先级）

> **M1(人审闭环+可见链)与 M2(文风/版式/配额/过滤)已全部 SHIPPED**(见 §5)。pipeline 上线,每日 **09:00 北京(01:00 UTC `finalize.yml`)** 自动出报,Pages 部署,live **https://ai-newsday.github.io/core/**。

> **竞品/BRD 分析见 `docs/competitive-analysis-ai-news.md`**(2026-06-24,12 个开源 AI 日报项目实读对比 + 2026-07-14 补充 alphasignal.ai)。该分析结论出的 5 条 P0 已**全部 SHIPPED**(放宽发卡池 #49、翻译失效根治 #60、metrics dashboard #59/#60、Reddit 生产 403 由 #55 `.rss` 方案解决、主动降噪 #61),见 §5。

> **2026-07-16 量级分析**:实测 juya 竞品单期 22 条 vs 我们单期 6-7 条,差距主因:(a) 人审确认门是有意的架构选择(零幻觉换量级,不打算改) (b) genre 覆盖窄——`writeup` 候选普遍 17-27 分过不了 `min_display_score:40` (c) 完全没有 X/融资/政策/硬件类一手信号源,juya 的"行业动态/产品应用/前瞻与传闻"我们没有对应桶。

> **2026-07-18/19 用户定调**:目标"每天更多信息、更优质信息、至少追平 juya 产出"。已确认方向:X **必须全覆盖**(已做,见下)、二手媒体/传闻类**必须全覆盖**(未做)、博客**必须全覆盖**(现状待查,见 P1 扩源)、微信公众号**明确不做**。当天已 SHIPPED:5 个 X List 建成+激活(lab/company/product/researcher/kol,见 `references/x-account-candidates.yaml`)、`x-extension` 两处生产链路 bug 修复(#66 `collect.yml` 路径、#67 `finalize.yml` 从未 clone x-signals)、`card_pool_limit` 25→50(#68,实测 322 候选仅 25 条进发卡池,明显欠采样)。**下一次 09:00 北京 finalize 是这些改动首次一起生效,先看真实产出再叠加新工作。**

> **2026-07-24 X 数据链路验证通过**:`x-extension` 加了 `chrome.alarms` 自动巡查(定时后台开 5 个 List tab → 抓取 → 关闭,不再需要用户手动开标签页,见 §5);轮换 `X_SIGNALS_PAT`(旧 token 一直 clone 认证失败,此前 X 数据从未真正进过 core 流水线);Collect 真实跑通验证:243 候选里 x.com 链接大量进入打分/解读/发卡,`tick_collect_done pushed=22`。顺带发现并修复 `popularity_weights` 缺 X 信号权重的 gap(#73,可见指标此前对所有 X 条目恒为 0)。

> **2026-07-25 用户逐条 review 07-24 日报,查实 4 个内容质量问题**(逐条查了真实数据/日志/API 才确认,不是猜的):(a) 论文条目把 Hugging Face(托管平台)误当成作者机构写"HF 发布论文"——查了 HF daily_papers API 和 arXiv 摘要页,两边都没有作者机构字段,真机构名需要接 Semantic Scholar `authors.affiliations` 之类的新数据源(需要 API key,试了一下匿名会 429),这是新 enrich 模块得 `/brainstorm`; (b) `release_importance`(#71)判定过松——07-24 13:46 那次 Collect `judged:194, filtered:11`,过滤率仅 5%,LangChain/LobeHub 的小版本更新是 LLM 真判成 tier≥2(不是 fail-open 兜底),说明分类 prompt 对"新增 XX 支持"这类措辞太宽松; (c) **实锤**:`langchain-gh` 源抓的几乎全是子包 tag(`langchain-openai==`/`langchain-fireworks==`/`langchain-anthropic==`...),真正的主包 `langchain==` 上一次发布是 8 天前——GitHub API 直接验证过,adapter 没有 tag 过滤机制,来什么收什么; (d) "依据"锚点和"来源"链接经常完全重复(同一个 URL 出现 2-4 次),渲染层没做去重。用户还提了"官方/模型 genre 是否该合并"——**这条没澄清清楚,待确认**,不要凭自己理解动 `genre_value`。

| ✓ | 优先 | 任务 | 详情 |
|---|---|---|---|
| ☐ | **P0** | **`langchain-gh` 加 tag 正则过滤,只收主包 `^langchain==`** | 2026-07-25 实锤(见上 c)。`github_releases` adapter 目前无 tag 过滤,`SourceSpec` 需加一个可选字段(比如 `tag_pattern`),只在 `langchain-gh` 上配置排除子包。小改动,直接开做。 |
| ☐ | **P0** | **发布渲染:依据锚点与来源链接重合时去重** | 2026-07-25(见上 d)。`src/pipeline/publish.py::_render_categories`:evidence 全部锚点都等于 `source.link` 时,只保留一个来源链接,不重复罗列"依据"。锚点包含其它不同链接时保持现状。 |
| ☐ | **P0** | **提示词:论文条目禁止误将 Hugging Face 当作者机构** | 2026-07-25(见上 a 的立刻可做部分)。`src/prompts/interpret_item.md` 加约束:没有机构信息时不要写"Hugging Face 发布/提出论文",避免把托管平台当成研究机构。真实机构名 enrich 是独立大项,见下一条。 |
| ☐ | **P1** | **论文机构归属 enrich(真机构名, 非平台名)** | 2026-07-25。需要新数据源(如 Semantic Scholar `authors.affiliations`,需申请 API key + 限流处理)在 interpret 前给 hf-papers 条目补机构信息。**新 enrich 模块,先 `/brainstorm` 排期,不要现在动**。 |
| ☐ | **P1** | **release_importance 判定标准复查** | 2026-07-25(见上 b)。5% 过滤率偏低,怀疑 prompt 对"partner 包级别小改动配大话术"过于宽松。**建议先做完 langchain tag 过滤(子包噪声去掉后再看过滤率有没有自然回升),再决定要不要动 prompt/阈值**,避免同时改两处混淆因果。 |
| ☐ | 待澄清 | **"官方"/"模型" genre 是否该合并** | 2026-07-25 用户提出但意图不明确(是嫌两个分类界限模糊,还是嫌读者一眼看得出不用标注?)。**下次对话先问清楚再动 `genre_value`/`group_by_category`**。 |
| ☐ | **P0** | **二手媒体/传闻类信源接入** | 用户明确要求全覆盖,直接对应 juya ~17% 的量级差距(Bloomberg/Reuters 报道传闻、政府公文如网信办备案)。**结构性障碍**:现有打分体系"一手性"维度(`genre_value` 里 paper/announcement 一手性权重 20)天然排斥二手转述,需要决定是 (a) 新增 genre/桶专门装"行业动态/传闻"配独立配额+更低真实性门槛,还是 (b) 放宽现有 genre 的一手性权重。**这是设计决策,先 `/brainstorm` 不要直接动 scoring.yaml**。 |
| ☐ | **P1** | **扩源探活 + 死源 legacy 化(含"博客全覆盖")** | 用户明说加源**必须先测过稳定提供 AI News**,且博客类信源要求全覆盖。做: (a) 探活脚本 = 该源近 30d yield 是否 >0 且 AI 相关性 > 阈值; (b) 加源门槛: 探活通过才 status=working, 否则 manual; (c) 长期 403 / manual 未维护的自动挂 legacy。**当前 22 死源 (gwern/garymarcus 等 substack 403) 手动挂 manual, 应自动化**,清完死源后再评估是否需要补充新博客源填补"全覆盖"缺口。自动发现新 KOL/repo/subreddit 延后到 P2。 |
| ☐ | **P1** | **X kol/researcher 名单继续扩充** | `references/x-account-candidates.yaml` 里 kol 目前只有 15 个(目标 50),中文圈仅 3 个明显偏薄;researcher/lab/company/product 相对完整。补充需要具体方向(用户点名关注的中文 AI 博主/研究者),不要凭空编 handle,每个都要 WebSearch 核实真实存在。 |
| ☐ | **P1** | 故事线合并(其余部分) | 相同事件多源聚合成时间线,提升"信息密度/质感"而非条数;剩余"多家媒体报同一新闻不同措辞"。竞品 `ai-news-radar` 参考。对应用户"更优质信息"诉求。 |
| ⚠ | ~~P1~~ | ~~评估 Folo cookie 读 X(首发信道)~~ | **改走浏览器 extension 路径,已 SHIPPED**(见上 2026-07-18/19 记录)。Folo 方案未采用。 |
| ☐ | **P2** | 社媒 first-class 输出 (**需对齐平台**) | 每日 top-3 出可发 Twitter / 微博 / 小红书的短卡 (140 字 + 图), 独立于长报。**用户明确要求先跟他对齐各平台字数/图片/风格差异, 不动**。用户 request 时再启动。 |
| ☐ | **P2** | 多频率输出 (4H / 周 / 月) | 拆自旧 "多频率 + 差异化输出", 社媒已独立。频率变化门槛: 源稳定率达标 (metrics 到位)。 |
| ☐ | **P2** | 自动扩源发现 (KOL/repo/subreddit) | 从已抓 items 挖被多次提及的 handle/repo/subreddit → PR-bot 半自动加源。**门槛: metrics dashboard 上线 (才能量化"发现的源是不是噪声")**。 |
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
| ☐ | GitHub/论文超额顺延到第二天 | 低 | 源于 `2026-07-14-paper-release-noise-reduction-design.md` §5:当前设计对超出 `adapter_quota`(github_releases≤2/github_trending≤1)或 genre 配额的条目直接砍掉,不顺延。若被砍内容仍有时效性(如未过审的 release),应进入第二天候选池而非丢弃。需要跨天持久化"待发布队列"新状态,**用户已确认是独立模块,后面再说**,不塞进当前 spec。 |

> 子项目 2 开放设计点:新 `tool` genre 的 `genre_value` 权重 + 配额槽(总配额 8 是否调整/挤占);repo `publisher` 如何承载 org 身份(GitHub `owner.type` → company/individual)。

---

## 5. ✅ Done

| ✓ | 任务 | 详情 |
|---|---|---|
| ☑ | X 数据链路首次真实产出验证 + 插件自动巡查(#73) | 根因排查:①`X_SIGNALS_PAT` 一直认证失败(旧 token 失效),core 从未真正 clone 到过 x-signals,此前"5 个 List 已激活"只是配置就绪、数据没流进来;轮换 token 后 clone 打通。②`x-extension` 抓取此前完全靠用户手动打开/刷新 List 标签页,人一忘就断供(实测断了近一周)——加 `x-visit` alarm,跟 `x-sync` 同频在后台自动开关 5 个 List tab,不再依赖人记得。③顺带发现 `config/scoring.yaml` `popularity_weights` 没配 `x_favorite`/`x_retweet`/`x_quote`,导致"可见指标"对所有 X 条目恒为 0,已修(contract test 防回归)。三者叠加后单次 Collect 验证:243 候选含大量 x.com 链接进打分/解读,`pushed=22`。 |
| ☑ | X 全覆盖:5 个 List + 生产链路修复(#65/#66/#67) | `ai-newsday/x-extension`4 个 bug 修完(MAIN-world 脚本从未被 WXT 注入过、GraphQL 字段 `user.legacy`→`user.core` 迁移、options 页面正则转义、诊断日志缺失);建成并激活 5 个 X List(lab/company/product/researcher/kol,`config/sources.d/x.yaml`);`collect.yml`/`finalize.yml` 两处 `X_LIST_DATA_DIR` 路径断链修复(真实 clone x-signals 验证过,9/25 候选来自 x_list)。 |
| ☑ | 扩大发卡池(#68) | `card_pool_limit` 25→50,实测 322 候选仅 25 条(8%)进发卡池,明显欠采样;也直接影响每日 Telegram 审阅卡片量,跟 juya-vs-us 量级差距分析联动。 |
| ☑ | 主动降噪·Paper + GitHub Releases 重要性(#61) | `raw_summary` 无上限撑爆 prompt 触发 fallback 已修(`InterpretConfig.raw_summary_max_chars`);`github_releases` 过滤 `prerelease`;`hf-papers` 加 `min_score:15`;GitHub 内容整体封顶(releases≤2/trending≤1,不挤占公告配额)。`_trim_to_sentence` 版本号截断 bug 一并修。见 `docs/superpowers/specs/2026-07-14-paper-release-noise-reduction-design.md`。 |
| ☑ | 产品质量 metrics dashboard(#59/#60) | 纯函数 funnel + rates、per_genre/per_source_top10/fallback_titles/trend_7d、matplotlib waterfall 图、TG photo 推送、`--tick metrics`。 |
| ☑ | 翻译失效根治(#60) | 多 provider LLM 链(ModelScope 4 家 alive + 3 未探活 + Agnes 付费保险丝)、`complete_json` parse 失败即切下一模型、`fallback_reason` 遥测接入 metrics。 |
| ☑ | Reddit 生产 403(#55) | 换 `old.reddit.com` HTML 抓取为 `.rss` 端点(数据中心 IP 不被封,但无 upvotes 信号),`config/sources.d/community.yaml` overlay。**未采用** PRAW OAuth 方案(`.rss` 更简单,现成解决)。 |
| ☑ | 放宽发卡池(#49) | 解耦"可审候选池"(`card_pool_limit`)与"发布 top-N"(`total_limit`),让低分但重要的首发不再在发卡前被砍。 |
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
