# Intent — 增信号源 + 漏斗可观测

- 日期:2026-06-18
- 来源:interview-me 会话(用户确认)
- 状态:意图已确认;各子项目待 spec→plan

源质量诊断(2026-06-17)后,用户两个独立诉求:**①扩充带信号的源** + **②漏斗可观测**。注意:"量"不是瓶颈(firehose 180 条全噪音、0 入选);要的是**带信号的优质候选**,不是无差别堆量。

## ① 增信号源(option A:聚合器带进新条目,不只打分)

现有 `enrich` 层已按 URL 把 `hn_points` 贴到已采条目(方案 B,只打分)。本项目新增"聚合器/仓库**当独立源**"(方案 A),与之并存。

| 源 | genre / publisher | signal | 备注 |
|---|---|---|---|
| HN 首页帖 | `writeup` / `individual` | `points` | 新 adapter,带新条目 |
| Reddit 高赞帖 | `writeup` / `individual` | `upvotes` | 新 adapter |
| GitHub Trending / 重要仓库 | **新 `tool` genre** / owner 身份 | `stars / forks / watches` | 新 adapter;owner(org 名如 openai / individual)要记录并参与权威 |
| 更多博客 | `writeup` / (lab\|company\|individual) | — | 纯 config;**必须先 validate(feed 真出合法 RSS、窗口内有内容)才入 `sources.yaml`,验不过不入或标 `manual`**(同现有 404/反爬→manual 惯例) |

**开放设计点(留给 spec):**
- 新增 `tool` genre 的 `genre_value` 权重 + 配额槽(总配额 8 是否调整 / 挤占)。
- repo 的 `publisher` 如何承载 org 身份 + 权威:① publisher 仍存 4 档 tier + 另用 per-org 权重表给 openai 加成;或 ② publisher 直接存 owner 名,权威按 owner 查表+默认。用户倾向"org 时值=具体 owner 名,个人=individual"。owner 类型可由 GitHub API `owner.type`(Organization/User)自动定档。

## ② 漏斗可观测(两产物)

- **每轮静态报告**(现在做):落 `run_dir` 的 HTML/markdown,本地 / CI artifact 直接看——这一轮"谁来的、被哪一关(窗口/去重/配额)砍了多少、为什么"。复用现有 `source_reports`(每源 count/status/error)+ 各层 `0X_*.jsonl` + score 的 `quota_applied` 事件,几乎不加新埋点。
- **常驻趋势看板**(展示延后):静态、cron 再生成,挂 Hugo 站;看跨轮趋势(谁来得多、谁几乎不来)。需先把"每轮 × 每源 × 各漏斗阶段计数"持久化进现有 `state.db`(SQLite)——数据攒取可现在起,渲染/展示等站点部署。
- **前置依赖**:静态站(Hugo+PaperMod,PR #5 建了 workflow)**尚未部署**,常驻看板无处挂。部署是单独任务。
- **Out of scope**:常驻 web 服务器、实时查询。

## 建议拆分(各自 spec→plan→PR)

1. **HN + Reddit 信号源** — 两 adapter,均 `(writeup, individual)`。最直接提质量。【用户选:先做这个】
2. **`tool` genre + GitHub 源** — Trending/repos + schema 动 genre/publisher;含开放设计点,需 brainstorm。
3. **博客扩充 + validation 闸** — config + 探活脚本,轻。
4. **每轮漏斗报告(②a)** — 独立、轻。
5. **跨轮看板 + 持久化(②b)** — 依赖站点部署。

## 全局约束(沿用)

- provider/adapter 解耦;打分/配额纯函数;阈值权重读 config 不写死。
- 新 adapter 走 TDD;对外副作用支持 `--dry-run`。
- 一次一个子项目;小 PR、issue-per-PR、从真实 `origin/master` 起有意义分支名。
