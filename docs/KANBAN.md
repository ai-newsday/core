# KANBAN — AI News Daily

> 唯一任务看板 + 进度表（合并自旧 `ROADMAP.md`）。源头意图见 `docs/intent/`，每层契约见 `docs/specs/`。
> 约定:一次一个子项目、小 PR、issue-per-PR、从真实 `origin/master` 起有意义分支名。
> 最后更新:2026-08-11。

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

> **2026-07-25 用户逐条 review 07-24 日报,查实 4 个内容质量问题**(逐条查了真实数据/日志/API 才确认,不是猜的):(a) 论文条目把 Hugging Face(托管平台)误当成作者机构写"HF 发布论文"——查了 HF daily_papers API 和 arXiv 摘要页,两边都没有作者机构字段,真机构名需要接 Semantic Scholar `authors.affiliations` 之类的新数据源(需要 API key,试了一下匿名会 429),这是新 enrich 模块得 `/brainstorm`,**提示词层面的止血已 SHIPPED,见下 #79**; (b) `release_importance`(#71)判定过松——07-24 13:46 那次 Collect `judged:194, filtered:11`,过滤率仅 5%,LangChain/LobeHub 的小版本更新是 LLM 真判成 tier≥2(不是 fail-open 兜底),说明分类 prompt 对"新增 XX 支持"这类措辞太宽松; (c) **实锤,已 SHIPPED(#80)**:`langchain-gh` 源抓的几乎全是子包 tag(`langchain-openai==`/`langchain-fireworks==`/`langchain-anthropic==`...),真正的主包 `langchain==` 上一次发布是 8 天前——GitHub API 直接验证过,加了 `SourceSpec.tag_pattern` 过滤; (d) "依据"锚点和"来源"链接经常完全重复(同一个 URL 出现 2-4 次),渲染层没做去重,**已 SHIPPED(#81)**。用户还提了"官方/模型 genre 是否该合并"——**这条没澄清清楚,待确认**,不要凭自己理解动 `genre_value`。

> **2026-07-26 X 打分压缩排查 + x-extension 生产 bug**:用户读真实发布内容问"为什么全都 80-100 分没有区分度"查出两处压分病灶——X 条目(机构分+genre 矩阵基线就到 93,只留 7 分给互动量排名),`adapter_authority_factor: {x_list: 0.0}` 单条推文不再吃机构信用(#77);`github_releases` 条目(`github_stars` 是仓库级固定值塞进"可见指标",FunASR 三个连续 patch release 分数完全一样,把真正的 `release_tier_score` 淹没),移出 `popularity_weights`(#82,实测 github_releases 占 top-50 发卡池 68%→48%)。`same_source_penalty` 对 X 账号(testingcatalog/fchollet 一天发多条真新闻)误判成"同源刷屏",加豁免(#83,实测 X 占 top-50 3-4→23 条)。发布配额 `total_limit` 11→12(#75,用户明确否掉了先提的 22,"先12吧")、X `reserved_quota:4` 保底通道不占 genre 配额(#76)。以上均按 [[verify-scoring-changes-against-real-dry-run]] 用真实 dry-run 数据核实过再合并。`x-extension` 两处生产修复:delta-capture(滚动到碰见已抓过的推文为止,50 条安全上限,替代猜的固定滚动深度)、`waitForBody()`(后台巡查标签页被 Chrome 节流,`document_start` 时 body 未生成导致滚动崩溃)。**AnthropicAI 低频重要账号捕获问题**:`waitForBody()` 合并后验证,fix 上线后两次真实巡查周期(13:05/16:05 UTC)x-signals 依然零捕获,说明这不是(唯一)根因——用户已定位到更早层面是"自动滚动没跑起来",具体修复待续。

> **2026-08-01 scope 对齐:用户人在 UK + 一次性提出 9 项诉求**。核心变化:**用户已不在北京时区**,而 `src/cli.py:529` 的 `_beijing_report_date()` 硬编码 `Asia/Shanghai`、三个 workflow 的 cron 注释也全按北京时间写 —— 现名义"北京 08:00 推卡"= 用户当地凌晨 01:00,"北京 20:00 推卡"= 当地 13:00,**整套节奏跟用户作息完全脱节**,这是"审阅链路对用户无效"的根因,比单条内容质量问题严重。同时查实两件事:(a) **"没反应的不结算"大部分已是现有行为** —— `select_report_items` 确认门只收显式 keep/edit,未点击条目本就不进报告;**唯一例外是零决策兜底**(一整天一条没点 → 自动发 top-N),只需改这个分支;(b) **存在真实设计张力**:「没反应的不发」与「希望发得多」相互拉扯,严格执行前者只会让量更少,**唯一同时满足的路径是"TG 推更多卡 → 用户审更多 → 发更多"**,即量来自用户判断量而非系统放宽自动发,这意味着**用户审阅工作量会实质变大**(已跟用户点明)。用户当日新增诉求已按下方 A/B/C/D 四组拆分录入,**执行顺序尚未拍板**(建议 A→B→C→D,A 是飞轮闸门)。**防编造设计(下方两条 P0)已聊到组件级但未写 spec,用户要求先对齐 scope,暂封存**。

| ✓ | 优先 | 任务 | 详情 |
|---|---|---|---|
| ☐ | **B组** | **TG 推送频次提高**(已部分完成) | 2026-08-01 用户提出。**collect 已从一天 3 次提到 5 次(#89,A组顺带做了)**,这半条基本完成。剩下的量杠杆见下面两条(card_pool_limit/quota 调整、news genre bug)。 |
| ☐ | **B组** | **X 单独设更高配额,不建质量黑名单**(2026-08-11 定调,暂缓执行) | 用户明确:X 的"低质量"排除**不建黑名单**(黑名单素材见下一条,单独走 grill-me);X 本身不是 genre,是横跨 paper/model/announcement/writeup 各 genre 的一个来源(一条推文可能转发论文/博客/模型/代码),**已经在靠真实分数竞争(#77/#83 修完同源惩罚豁免+机构分打折后),不需要给它开小灶**。用户认为提高 X 存在感的正确杠杆是**调高它所在那几个 genre 的 quota/total_limit**,不需要给 X 单独发明新机制。去重不是问题,`same_source_penalty` 已豁免 x_list,dedup 本身跨 adapter 通用。**排在 quota 数字定下来之后处理,不单独起线**。 |
| ☐ | 待澄清 | **低质量来源黑名单素材(AWS/重复/API 站模型转发)** | 2026-08-11 用户提到:目前看到的低质量主要是 AWS 大部分文章、一些重复内容、API 站的模型再分发。**用户明确不要现在建黑名单**,后续通过 `grill-me` 或用户给具体案例的方式逐步建立。先记案例来源方向,不要凭自己理解猜测具体规则或动手实现。 |
| ☐ | **D组** | **TG 大量条目未解读(extractive_fallback)** | 2026-08-01 用户反馈"今天 TG 还有很多没解读的"。`interpret_item` 任何异常(LLM 全链失败/JSON 解析失败/tags 数不符)都会落到 `extractive_fallback`,卡片打 `⚠️ [未解读]` 徽章。`config/interpret.yaml` 已配 4 主模型 + 4 备用(含 agnes 付费保险丝),`fallback_reason` 字段已记录异常类型但**只进 InterpretResult 计数,没进 metrics 面板**。**下一步是诊断不是修**:跑真实 tick 看 `interpret_error` 事件的 `error_type` 分布,确认是模型全挂、限流、还是 schema 校验(如 `tags count not met`)误伤 —— 不同根因修法完全不同。参考 [[run-real-dryrun-diagnostics]]。 |
| ☑ | **P0** | **release_importance 模型链全死,fail-open 从未真正判定过** | 2026-08-11 跑配额 dry-run 时意外实锤:`config/enrich.yaml` 的 `release_importance` 链——主模型 `deepseek-ai/DeepSeek-V4-Flash`、备用 `moonshotai/Kimi-K2.6`——**拿真实请求逐个测过,ModelScope 上都已下线**("has no provider supported"),再备用 `agnes` 又没配 `AGNES_API_KEY`。三个全挂 → 每条 GitHub Release 无差别 fail-open 到 tier=2,`release_importance` 这段时间根本没在判定,不是判定标准松。**2026-08-12 SHIPPED**:换模型分三轮才真正修好,记录下"活着≠能用"这个教训——(1) 先换成简单 ping 测活的 `Step-3.7-Flash`/`Intern-S1-Pro`,结果拿真实 release body 跑发现两个都不遵守 JSON 输出约束(空内容/解析不出来);(2) 再换成 curl 单次测试正常的 `DeepSeek-V4-Pro`,结果拿真实 body 连续跑 12 次调用**100% 失败**(空内容/`NoneType`),原来单次 curl 测试和"在完整 pipeline 里反复调用同一提示词"是两回事;(3) 最终定案:`models: [Qwen/Qwen3.5-397B-A17B]` 主力(5/6 次真实调用成功,tier 判定有真实区分度,不再是清一色 fail-open),`fallback_models: [agnes:agnes-2.0-flash]` 兜底;顺手把 `timeout_s` 30→60(对齐 interpret 主链,30s 下真实 body 几乎全 ReadTimeout)、`max_tokens` 300→500(真实 reason 文本比预估长,曾截断出 `Unterminated string`)。验证:真实 collect 出的 6 条 github_releases 过 `judge_release_importance`,5 条真判定(tier_score 4 vs 9 有区分度),1 条被真实判定为 tier≤1 正确硬过滤(不是 fail-open)。**下一步**:重新跑一次配额 dry-run,下面「草稿池/终版配额哲学」的数字是在这个 bug 存在的情况下测的,基线可能会变。 |
| ☐ | **P1** | **release_importance 判定标准复查(可能已被上一条解释)** | 2026-07-25(见上 b)。5% 过滤率偏低,当时怀疑 prompt 对"partner 包级别小改动配大话术"过于宽松。**2026-08-11 新发现**:更可能的解释是模型链根本没跑起来(见上一条),不是判定松。**等上一条修完、重新观察真实过滤率再看这条是否还成立**,不要在旧假设上继续动 prompt/阈值。 |
| ☑ | **P0** | **22:00 提醒"还有 N 条待审"数字虚高(报了 40)** | 2026-08-12 用户实测反馈"40 条不是真实数值"。查真实 Remind workflow 当天日志:`{"event": "reminder_decisions_fetch_error", "error": ""}` → `undecided_count: 40`。空字符串 error 是 `httpx.ReadTimeout` 的典型指纹(`str(e)` 默认为空,不是认证失败——403 的 `HTTPStatusError` 有明确消息文本,单独 curl 验证过)。根因:`WorkerDecisionStore` 默认 `timeout_s=10.0` 对 Cloudflare Worker 的真实决策查询不够,超时后 `run_reminder_tick` 按设计保守把当天全部推送条目算未决——40 是真实推送总量,不是真实待审数。SHIPPED:超时 10s→30s(同一个类同时被 finalize/remind 两条 tick 复用,一次修好两处),给两处 fetch-error 日志加 `error_type` 字段(这次靠猜 httpx 内部行为才诊断出来,以后应该直接从日志读)。 |
| ☑ | **P0** | **两天没发草稿链接——真根因是 Worker `/decisions` 串行 KV 读取,不是客户端超时不够** | 2026-08-14 用户反馈"这两天不发草稿的链接了"。查 8-13 22:51 那次 finalize workflow 真实日志:`{"event": "decisions_fetch_error", "error_type": "ReadTimeout", "error": ""}` → `publish_done item_count: 0`——**这条 run 用的就是上一条已经把超时提到 30s 的代码(headSha 对上了),依然超时**,说明上一条修的不是根因,是掩盖了根因。查 `workers/telegram-webhook/src/index.js:57-72` 的 `handleDecisions`:对 KV `list()` 返回的每个 key **串行 await** 一次 `.get()`,决策 TTL 7 天,决策越攒越多这个循环越跑越慢,直到连 30s 都不够。`run_finalize_tick` 拉不到决策时按设计保守当"全部未决"、不擅自发布——这是设计在正确工作,只是它拿不到确认因为 Worker 本身卡住了,不是没生成。SHIPPED:`Promise.all` 并发拉取同一页的 `.get()`,不再逐个等;Worker 自带 vitest 5 个测试全过。 |
| ☐ | 待决策 | **`writeup`(41) vs `announcement`(58) genre_value 17 分结构性差距** | 2026-07-26 新发现(见上)。同源惩罚豁免(#83)修完后,这是继续压低 X 个人研究者账号(fchollet 这类)分数的最后一个结构性瓶颈。`genre_value` 是全局的,会影响所有 writeup 来源不只 X,**要不要动是设计决策,不要凭自己理解改**。 |
| ☐ | 待澄清 | **"官方"/"模型" genre 是否该合并** | 2026-07-25 用户提出但意图不明确(是嫌两个分类界限模糊,还是嫌读者一眼看得出不用标注?)。**下次对话先问清楚再动 `genre_value`/`group_by_category`**。 |
| ☐ | **P1** | **hf-papers 标题把方法/模型名前置(不需要新数据源)** | 2026-07-29 用户指出论文条目"方法/模型名不突出"。跟"缺机构名"(下面 P1)是两个独立问题——这条**不需要接新数据源**,是提示词层面的事:`src/prompts/interpret_item.md` 加约束,标题必须把论文提出的方法/模型名放在最前面当钩子(参考 SOP 里"0.22B 反超 11.9B"这类样例)。可以直接排期。 |
| ☐ | **P1** | **GitHub Releases 只放重要仓库/重要版本** | 2026-07-29 提出,08-01 已确认方向:**要一份具体"重要仓库"名单**,不是纯靠 tier 判定。08-01 补充实锤:`openhands-gh`(All-Hands-AI/OpenHands)一次审阅出现 v1.7.0/v1.7.1/v1.7.2/v1.8.0 四个版本候选——查了最近 12 天真实发布的 `content/posts/*.md`,一次都没出现过 openhands-gh,说明 `adapter_quota:{github_releases:2}` 在**发布层**是生效的,问题在**审阅层**:`release_importance`(#71)的 tier 判定没把"修复 XX 问题"这类补丁版本挡在候选池外,浪费审阅精力。修复位置应该往前挪到候选/打分阶段,不只是发布时硬砍到 2 条。**用户明确要求先做内容质量/稳定性那几条(上面 P0),这条排在后面**,待办:整理"重要仓库"名单(具体范围下次问)。**2026-08-01 用户再次提出"GitHub Releases 低版本还没筛掉"——与本条完全重复,不新开条目**,归 **C组**。 |
| ☐ | **B组** | **二手媒体/传闻类信源接入(实为 news genre 发卡池 bug)** | 用户明确要求全覆盖,直接对应 juya ~17% 的量级差距(Bloomberg/Reuters 报道传闻、政府公文如网信办备案)。**2026-07-29 用真实数据核实,原假设(缺源)是错的**:`config/sources.yaml` 里已有 10 个 `genre: news` 媒体源(TechCrunch/VentureBeat/The Verge/Ars Technica/MIT Tech Review/MarkTechPost/Wired-AI/the-decoder/smol-ai-news/lastweekin-ai),真实 collect 抓到 64 条候选,但**打分后最高才 62 分、全局第 89 名**,`card_pool_limit=50` 在第 50 名/79 分截断——64 条 news 无一进过发卡池,这也是最近 8 天日报"新闻"分类从未出现过的直接原因(`可见指标` 对 RSS 媒体文章恒为 0,`news` genre_value 基础分也不高,两者叠加把整个 genre 结构性挡在发卡池外)。**这跟"缺二手媒体源"是两个不同的问题**:源和配额都不缺,缺的是这些已经在跑的源永远进不了发卡池——更像一个独立的打分 bug(见下一条同类分析),原 (a)/(b) 两个方案选项暂缓,先看要不要单独修 news 的可见指标/发卡池保护。真正意义上"我们完全没覆盖的二手/传闻类"(财经媒体报道的融资传闻、政府备案文件)跟这 10 个源不是一回事,P0 本身是否还要做、范围是什么,需要用户重新确认。**2026-08-01**:用户"整体发多一点、让我多判断"的诉求与本条同源(都是量),归 **B组**,与上方「TG 推送频次提高」一起做。**2026-08-11 排在 quota 数字定下来之后一起改**:这条是"进候选池"的坎(`card_pool_limit` 前),跟下面「草稿池/终版配额哲学」是"发布时"的坎(`total_limit`/`quota`),两个不同关卡,提高后者救不了这条,需要单独动 `genre_value` 或加发卡池保护。**用户已确认方向**:如果是真的没价值,不进是对的;但如果只是因为缺点赞转发数据被冤枉,该用新方式衡量——**跟 D组「内容确定性维度」是同一个 brainstorm,见那条**。 |
| ☐ | **B组** | **草稿池/终版配额哲学重新设计**(需要真实 dry-run 数字, 待用户选定) | 2026-08-11 用户定调:`card_pool_limit`(草稿池, 给用户审阅用的候选量)不用太低, 可以稍微放大;`total_limit`/各 genre `quota`(终版, 定稿真正发布的上限)要按类分别判断,**不是所有类都同样放大**——论文一天最多 3 篇(现状已是 3, 但没好论文就该少发, 宁缺毋滥, 现有 `min_display_score` 地板+quota 上限的组合本来就是"上限不是目标"的语义, 可能已经满足这条不需要改代码);Code、模型同理宁缺毋滥;**公司新闻(announcement)可以适当放宽**。**下一步**:跑真实 dry-run, 用当前候选量实际测 `card_pool_limit`/`total_limit`/`quota:{announcement}` 调整后的具体数字效果, 给用户看真实前后对比再定档(不是凭空提议数字, 参考 [[verify-scoring-changes-against-real-dry-run]])。 |
| ☐ | 待澄清 | **HF 按机构(`?author=`)单独建源, 补充/替代全局 trending 榜** | 2026-08-11 用户提出。**已验证可行**:HF 模型列表 API 支持 `?author=<org>` 参数(实测 `?author=LiquidAI&sort=createdAt` 真实可用, 返回该机构最新模型)。现在 `hf-models` 源(`config/sources.yaml`)只有一个全局 `sort=likes7d` 榜单, 这也是 LiquidAI 官方旗舰模型(07-29 那次)输给个人量化 repack 的根因之一(见下面机构绑定那条)。**待澄清**:按机构建源要覆盖哪些具体机构(不要凭自己理解拍名单, 需要用户点名), 是新增独立源并存, 还是替换现有 trending 源, 配额怎么分配。 |
| ☐ | **P1** | **论文/代码/模型条目绑定真实机构名(不只是论文)** | 2026-08-11 用户扩展了原有的"论文机构归属"待办(见上方 07-25 记录, Semantic Scholar 方案未开始):**不只 paper genre, code(github_releases)和 model(hf-models)也要绑定真实发布机构, 论文尤其优先**。现状:`publisher` 字段只有 lab/company/individual/media 四个粗粒度值, 不是"这是哪家公司/机构"的具体名字。跟上一条(HF 按机构建源)是近亲但不同:那条是"抓取范围"问题(按机构主动去抓), 这条是"归属标注"问题(抓到的条目标清楚是谁发的)——两条可以互相促进(按机构抓的条目天然知道机构是谁, 不需要再额外识别), 排期时一起看。真机构名仍需要接新数据源(paper 用 Semantic Scholar `authors.affiliations`, 需要 API key), 是独立的 enrich 模块, 先 `/brainstorm`。 |
| ☐ | **D组** | **interpret 标题编造动作词(如"发布")** | 2026-07-29/31 用户发现日报"OpenAI 发布 GPT-5.6"这条,拿真实 OpenAI RSS 核实过:原文标题是 "How GPT-5.6 fuses frontier intelligence with frontier efficiency"(07-29 发的效率跟进文章),GPT-5.6 真正的发布文章是 07-09 发的,早了三周,原文完全没提"发布"。interpret 层凭空加了"发布"这个动作词——**这是编造,不是回退触发的"少写",是多写**,直接违反 CLAUDE.md"宁可少写不可编造"。现有 `evidence` 校验只查锚点是不是合法链接,不查标题/正文的**动作性表述**(发布/推出/开源等)有没有原文支撑。需要给 `interpret_item.md` 加约束:标题里的动作词必须能在 `raw_summary`/`related_links` 里找到依据,没有就用中性表述(如"更新"/"介绍")。 |
| ☐ | **D组** | **打分没有"内容确定性/信息密度"维度,编造/模糊内容能拿满分** | 跟上一条同一实锤:那条编造的 GPT-5.6 条目正文里 LLM 自己写"当前信息未披露具体技术细节或基准数据,需后续验证实际效果"——模型自己都不确定,却拿了 100 分(满分)。现有 `score_breakdown`(机构影响力/一手性/技术价值/产业影响/扩散潜力/可见指标/时效/惩罚/读者相关度)没有任何维度衡量"这条内容本身写没写清楚、信息够不够扎实",纯靠来源权威+时效就能堆到顶格,内容质量对分数完全没有约束力。需要想清楚怎么把"确定性/信息密度"折进打分或者作为一道独立过滤,不是简单加一维——**这是设计决策,先 `/brainstorm`**。**2026-08-11 用户把这条跟"news genre 需要新方式衡量价值"(见上「news genre 发卡池 bug」)连到一起**:news 类没有点赞转发数据不代表没价值,用户明确"如果是因为没有点赞转发的话,应该用新的方式衡量"——需要一个能真正读内容判断"实不实、值不值"的 LLM 判定器,同时接住"内容编造/空洞"(这条)和"没有互动数据不该被判低价值"(news genre 那条)两个问题,**不是两条独立的活,brainstorm 时一起想**。 |
| ☐ | **P1** | **同一故事线的跟进文章被当独立新闻反复发布,挤占同 genre 配额** | 同一次实锤发现:GPT-5.6 一个模型的三篇不同时间跟进文章(07-09 发布/07-29 效率优化/ARC-AGI-3 调参)被当三条独立新闻分别打分发布,一天之内把 `announcement` genre 配额(quota:3)全部占满,挤掉其它公司当天的官方公告。`same_source_penalty` 只压分不限量,防不住"同一公司同一模型的连续跟进"这种情况。跟已有的"故事线合并"(上面,聚焦多源报同一事件)是近亲但不同:那条管"多个信源报同一件事",这条管"同一信源同一主体的时间线跟进"。 |
| ☐ | **P1** | **扩源探活 + 死源 legacy 化(含"博客全覆盖")** | 用户明说加源**必须先测过稳定提供 AI News**,且博客类信源要求全覆盖。做: (a) 探活脚本 = 该源近 30d yield 是否 >0 且 AI 相关性 > 阈值; (b) 加源门槛: 探活通过才 status=working, 否则 manual; (c) 长期 403 / manual 未维护的自动挂 legacy。**当前 22 死源 (gwern/garymarcus 等 substack 403) 手动挂 manual, 应自动化**,清完死源后再评估是否需要补充新博客源填补"全覆盖"缺口。自动发现新 KOL/repo/subreddit 延后到 P2。**2026-08-01 用户再次提出"Blog 等官方源还是太少"——与本条"博客全覆盖"部分完全重复,不新开条目**,归 **B组**。 |
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
| ☑ | 去分类标题 + 来源链接改末尾参考章节(#90) | 2026-07-29 提出,08-05 拿真实数据核实执行:`## 论文/模型/官方` 大分类标题去掉(genre 由谁先抓到决定,标题反而误导,如 Mistral Shieldstral 模型发布因走博客 RSS 被标成"官方");链接收进文末 `## 参考链接` 编号列表,正文只留 `[n]`,`[]` 内用条目标题(不是源名)。之前这条一直标"待澄清"没人动,是漏更新,实际早就上线了。 |
| ☑ | A组:时区与审阅节奏改到 UK(#89/#91/reminder PR) | 2026-08-01 用户人在 UK,原节奏(北京 08/13/20 推卡、北京 09:00 结算)换算成当地时间是凌晨 01:00/06:00/13:00 推卡、02:00 结算——两次推送和结算本身都发生在用户睡觉时,是"审阅链路对用户无效"的根因。四条一起改完:①`PublishConfig.timezone`(默认 `Europe/London`,config 可配,IANA 名跟随夏令时)替换硬编码 `Asia/Shanghai`(#89);②cron 改成 UK 08/11/14/17/20 推卡(5 次/天)+ 23:00 结算,`date_label` 从"昨天"改成"当天"——这个改动顺带解决了本表下方曾经记录的"finalize 混入超出目标日期条目"逻辑漏洞(23:00 结算的报告天然只包含当天内容,不需要额外上界过滤)(#89);③零决策兜底(一整天没碰 TG → 自动发 top-N 草稿)整个删掉,`select_report_items(items,{})` 天然返回空列表,不需要特判——用户原话"我没做出反应的最好不要结算"(#91);④新增 `--tick remind`,UK 22:00(23:00 结算前 1 小时)推一条"还有 N 条待审"提醒,只报个数不列标题,决策拉取失败保守地把当天全部条目算作待审(不假装 0 条漏发提醒)。 |
| ☑ | hf-models README 抓取, 根治空 body 仍照发(如 `microsoft/Mage-Flow`) | 2026-07-27。根因:`hf_models` adapter 只调模型列表 API, `raw_summary` 恒为 `None`, LLM/回退都无米下锅——但模型在 HF 上其实常有真实 README(实测 `microsoft/Mage-Flow` 有 34KB 正文, 从未被抓)。方案:走用户要求的根本路线(不是 `build_report` 加 `bool(body)` 止血),新 enrich 阶段 `enrich_hf_models_readme()` 在 card_pool 截断前抓每个候选模型的 README, 清洗 frontmatter/图片/HTML/实体后填 `raw_summary`;抓不到或清洗后太短(`min_body_chars`, 默认 80, 走 config)的条目直接从候选剔除, 不占审阅卡位。真实 dry-run(2026-07-27, 无需 LLM key, 纯 collect+README 抓取)对当天真实收集的 2 个 hf-models 候选(`unsloth/Kimi-K3`/`-GGUF`)验证:清洗前 README 里 `&nbsp;`/空白噪声大量残留(用真实数据核实并修了清洗正则, 不是猜的), 清洗后两条都保留, 原始清洗结果 14492 字符。当天样本量小(仅 2 条 hf-models 候选), `min_body_chars=80` 尚未被真实"太短该剔除"的候选压过, 留待样本更多后再看要不要调。**全分支最终 review 又揪出两个真实缺口(已修)**:清洗后文本没封顶, 14.5KB 会原样冲进 dedup 的 embedding(单条超大文本能让整批 embedding 退化成 None, 近似模板化的 README 还可能把不同型号误聚成一条)——加 `HFReadmeConfig.max_body_chars`(2500, 走 config)截断;`_clean_readme` 正则是纯 LF, CRLF 写的 README frontmatter 会漏进正文——先统一换行符再清洗。 |
| ☑ | X/github_releases 打分压缩修复 + 发布配额调整(#75/#76/#77/#82/#83) | 2026-07-26(见 §3 上方叙述)。`adapter_authority_factor: {x_list:0.0}`(#77,X 单推文不吃机构信用)、移出 `github_stars` 可见指标(#82,github_releases 占 top-50 68%→48%)、`same_source_penalty_exempt_adapters: [x_list]`(#83,X 占 top-50 3-4→23 条)、`total_limit` 11→12(#75)、X `reserved_quota:4`(#76)。均有真实 dry-run 前后数字支撑。 |
| ☑ | x-extension:delta-capture + `waitForBody()` 修复 | 2026-07-26。滚动到碰见已抓过的推文为止(50 条安全上限,替代猜的固定滚动深度);后台巡查标签页被 Chrome 节流导致 `document.body` 未生成时滚动崩溃,加 `utils/dom.ts:waitForBody()`。**AnthropicAI 低频账号捕获仍未确认解决**,见 §3 待办。 |
| ☑ | 内容质量修复:HF 机构误标 + langchain 子包过滤 + 依据去重(#79/#80/#81) | 2026-07-25 用户逐条 review 查实(见上叙述)。`interpret_item.md` 禁止把 HF 平台当论文机构(#79)、`SourceSpec.tag_pattern` 只收 langchain 主包(#80)、依据锚点与来源链接重合时去重(#81)。 |
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
