# Claude Code Session 启动包 — AI News Daily MVP

> 目标：开一个 Claude Code session，按范式**逐层滚出 MVP**。不要一句"做完 MVP"——那会触发擅自假设/过度工程/铺太大。MVP = 下面 6 个小循环。

---

## 0. 开跑前锁 3 件事（5 分钟）

1. **语言**：默认 **Python**（已写进 `CLAUDE.md`）。若想用 TS，把 `CLAUDE.md` 的"技术栈"段改掉再开跑。
2. **先服务哪一类读者**：默认 **研究者 / 工程师**（决定打分口径与信息密度）。要改就改 PRD §1.2。
3. **拿好 API key**：Claude API；Qdrant（本地 Docker 即可）；可选 Firecrawl（难抓源兜底，免费 1000 credits/月）。

## 1. 装开发 skill（一次性）

```bash
# 核心 4 个
npx skills add obra/superpowers
/plugin marketplace add forrestchang/andrej-karpathy-skills
/plugin install andrej-karpathy-skills@karpathy-skills
npx skills add https://github.com/anthropics/skills --skill webapp-testing
npx skills add https://github.com/anthropics/skills --skill skill-creator
# 接外部内容/上 OAuth 前再加：
# npx skills add trailofbits/skills
```

## 2. 放好文件 + 起仓库骨架

把这四份放进新 repo：`CLAUDE.md`（根目录）、`docs/PRD.md`、`docs/Claude-Code-开发范式.md`、`docs/specs/collection.md`。然后让 Claude Code 起骨架（**只建目录和占位文件，先不写逻辑**）：

```bash
# 也可直接跑这段 bootstrap，省一轮对话
mkdir -p ai-news-daily/{docs/{adr,specs},references,src/{pipeline,adapters/{sources,llm,vector,channels},prompts,core,observability},tests/{contract,golden,eval,snapshot},fixtures,config,.claude/skills,.github/workflows}
cd ai-news-daily && git init -q && uv init -q 2>/dev/null
# 把 CLAUDE.md / docs/* 拷进来后：
git add -A && git commit -qm "scaffold: repo skeleton + docs"
```

## 3. 第一圈：采集层（逐句粘贴，别跳步）

> 每个 prompt 跑完**停下来看**，确认了再发下一个。

**① 对齐（不写码）**
```
读 CLAUDE.md、docs/PRD.md、docs/specs/collection.md。
用 /brainstorm 把采集层里你不确定的点和你要做的假设全列出来，等我确认。先不要写任何代码。
```

**② 出计划（不写码）**
```
/write-plan 实现 docs/specs/collection.md 的采集层。
拆成 2-5 分钟的小任务，每个带文件路径和验证步骤。给出 provider 接口签名。计划给我看完再停。
```

**③ TDD 实现**
```
/execute-plan。严格 TDD：先按 spec §8 的 golden 用例 + §9 写 contract/golden 测试（用 fixtures/，时间用注入的 now），跑红；再实现到跑绿。
支持 --dry-run。完成后把测试结果和一次 dry-run 的 CollectionResult 贴给我，然后停。
```

**④ 你 review** → 只看两样：**代码 diff** + **dry-run 产物**。不满意就指出具体点让它改（回 ③）。满意 → 让它开小 PR、CI 绿、合并。

## 4. 后续 5 圈（同样的 ①②③④ 节奏，一圈一层）

| 圈 | 层 | 先做的事 | 关键验收 |
| --- | --- | --- | --- |
| 2 | 去重聚类 | 先写 `docs/specs/dedup.md`（让 Claude 用 spec-writer 起草，你定稿） | 去重覆盖率 100%（golden） |
| 3 | **打分 + 配额** | 写 `docs/specs/ranking.md`；纯函数 + 起 eval 金标准 | 配额 100% 符合 config；score_breakdown 齐全 |
| 4 | 解读生成 | 产品 SOP 进 `references/editorial-guideline.md`；解读后过去 AI 味 | 必读条目含 takeaway+evidence；LLM 失败回退、零编造 |
| 5 | 审阅页 | 最简 Web 页读 dry-run JSON，留/删/改/排序；用 webapp-testing 测 | 单期审阅 ≤ 10 分钟 |
| 6 | 单渠道发布 | 先 Notion 或 Markdown 一个渠道 | 发布成功率 ≥ 95% |

> 第 3、4 层是日报灵魂（选得准 + 没 AI 味），多花点时间。其余按 spec 推进。

## 5. MVP 收尾验收

对照 **PRD §2.1 的 11 条**逐条打勾。最后一条是真正的成败线：**连续 7 天每天产出并发布，且每天审阅 ≤ 10 分钟。** 达标 = MVP 完成，再进 P1（反馈闭环/多端/沉淀）。

## 6. 三条反模式（别犯）

- ❌ "帮我把整个 MVP 做出来" → ✅ 一圈一层，spec 先行。
- ❌ 让它自由发挥写 LLM 自由文本 → ✅ 结构化 JSON + 校验 + 失败回退。
- ❌ 跳过测试先跑通 → ✅ 没有失败测试不写实现（TDD），CI 绿才合并。
