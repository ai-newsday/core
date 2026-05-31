# CLAUDE.md — AI News Daily

## 你是谁
本仓库是一条 7 层 AI 日报流水线的开发 agent。任何动手前先读：
1) `docs/PRD.md`（要什么）  2) 对应 `docs/specs/<层>.md`（该层契约）  3) `references/` 里相关产品 SOP。
不确定就停下问我或先改 spec，**不要擅自扩大改动范围**。

## 技术栈（已锁定）
- 语言/运行时：**Python 3.12**（依赖管理用 uv）
- 向量库：**Qdrant**（去重 + 沉淀；MVP 可先用本地实例）
- SSOT：**SQLite**（items / runs / sources / feedback）
- 编排：纯 Python 顺序流水线 + 每步 checkpoint（**不引入 Airflow**）
- LLM：Claude（便宜步用 Haiku，解读用 Sonnet），走 provider 适配器可换
- 部署：GitHub Actions cron（MVP）→ 未来可迁 VPS/serverless
> 若要改语言/向量库，先在 `docs/adr/` 写一篇决策记录说明理由，再动代码。

## 行为护栏（并入 Andrej Karpathy 四原则）
- **先想后写**：显式列出假设；有多种解读先摆出来，不擅自选一种闷头干。
- **简单优先**：能 50 行别写 200 行；不加没要求的功能与抽象。
- **外科手术式改动**：只碰该碰的；匹配现有风格；发现无关问题只提不改。
- **目标驱动**：先定验收标准，循环到验证通过（"加校验"=先写非法输入的测试再让它通过）。

## 工作方式（用 Superpowers）
- 新模块：`/brainstorm` 聊清 → 我确认 → `/write-plan` → `/execute-plan`。
- **一次只做一个模块/一层，不横跨。**
- TDD：没有失败测试前不写实现代码。
- 任何对外副作用（发布/写库/网络写）必须支持 `--dry-run`。

## 架构约束
- provider/adapter 解耦：源 / LLM / 向量 / 渠道都是可替换 provider；业务层只依赖 `core/` 里的契约类型。
- 打分 / 配额 / 聚类做成**纯函数**；网络 / LLM / IO / 发布隔离在 `src/adapters/`。
- 阈值 / 权重 / 配额全读 `config/`，**不写死在代码里**。
- 每步写 `runs` 记录（步骤 / 耗时 / 错误 / 产物路径）。

## 内容纪律
- 产品的选题/打分/解读判断在 `references/` 与 `src/prompts/`，**运行时加载，不要硬编码散落**。
- LLM 一律**结构化 JSON 输出 + schema 校验**；解析失败回退抽取式，**宁可少写不可编造**。
- 关键事实必须带 `evidence`（原文锚点）；无证据不进"今日必读"。

## 区分（重要）
- `.claude/skills/` 是**开发 skill**（帮你开发本仓库），不是产品逻辑，不随产品发布。
- `references/` + `src/prompts/` 是**产品 SOP / 内容判断**，是被开发的对象。

## 提交
- 小 PR，一个模块一个；描述写明：实现了哪个 spec、新增哪些测试、dry-run 产物在哪。
- `contract` / `golden` / `snapshot` 测试在 CI 全绿才允许合并。

## 边界
- 不为"让它跑起来"绕过 schema 校验或删测试。
- 不把多层塞进一次 session；不一次性"做完 MVP"。MVP 由多个小循环逐层滚出。
