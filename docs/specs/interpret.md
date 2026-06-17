# Spec — 解读生成层 (Interpret / Generation)

> 放置路径：`docs/specs/interpret.md`。这是七层流水线的第 4 层，MVP 第四个要实现的模块。
> 对应 PRD §3.3（解读生成步骤）、§3.4（LLM 失败回退、证据缺失降级）、§4.4（分层呈现 / 可操作性 / 锐评 / 证据链 / 生成纪律）、§5.2（今日看点）、§5.5（NewsItem 模板 title/summary/takeaway/hot_take/tags/evidence）、§2.1（验收 #5 解读零幻觉）。
> 上游：第 3 层打分 (`docs/specs/score.md`) 产出的 `ScoreResult.selected_items: list[ScoredItem]`（类型配额筛选后的高分主条目，≈7 条，带 `score`/`score_breakdown`/`related_links`）。下游：第 5 层审阅（人工对 `InterpretedItem` 留/删/改/排序）、第 6 层发布（多端渲染器消费统一内容模型）。

## 1. 目的

把上游"已打分入选"的 `ScoredItem` 列表，逐条生成 PRD §5.5 的解读字段（中文标题 / 摘要 / takeaway / 锐评草稿 / tags / 证据链），并产一句话级"今日看点"宏观趋势。本层是流水线**第一个引入 LLM** 的层。

直接服务的痛点：纯聚合谁都能做，**人写的判断与态度（锐评）+ 可操作性解读**才是护城河（PRD §1.3、§4.4）。本层成败标准是 **PRD #5：解读零幻觉**（"今日必读"每条含 `takeaway` + `evidence`，无证据不得入必读，LLM 失败回退抽取式，人工抽检 0 条编造事实）。

## 2. 范围 / 非目标

- **做**：逐条 LLM 解读（结构化 JSON + schema 校验）、抽取式回退、证据链锚定与必读门、一次日报级"今日看点"、产出 `InterpretResult`、写 `runs` 事件、支持 `--dry-run`。
- **不做（本圈明确延后）**：
  - **日报级组装 / 多端渲染**（今日必读 Top3 分组、分类速览排版、Notion/公众号/网站/RSS 渲染）：属第 6 层发布/渲染（PRD §5.1 一稿多渲染）。本层只产逐条字段 + 今日看点，分组留下游。
  - **hot_take 人工定稿**：本层只产 AI **草稿**；人工定稿在第 5 层审阅（PRD §4.4「可 AI 起草、人工定稿」）。
  - **抓取原文正文**：本层只用上游已带的 `raw_summary` / `title_en` / `link` / `related_links` 文本；抓全文属采集层职责（CLAUDE.md「一次只做一层」）。
  - **reader_relevance / 个性化**：MVP out-of-scope（PRD §2.1），打分层已置 0，本层不涉及。
  - **真实 Anthropic 原生接入**：本圈走 OpenAI 兼容 chat 端点（复用 embedding 的 ModelScope 认证），偏离 CLAUDE.md「解读用 Claude」锁定，理由记于 `docs/adr/0001-llm-openai-compatible.md`；provider 仍可换。

## 3. 接口契约

```python
def interpret(items: list[ScoredItem], config: InterpretConfig, ctx: RunContext,
              llm: LLMProvider) -> InterpretResult: ...
```

- **输入**：
  - `items: list[ScoredItem]` —— 上游打分入选条目（`ScoreResult.selected_items`，已按 score 降序）。
  - `config: InterpretConfig`（见 §6，全部模型参数 / 字段约束读 `config/interpret.yaml`，不写死）。
  - `ctx: RunContext` —— 复用上游的 `run_id` / `now` / `logger`。
  - `llm: LLMProvider` —— 注入的 LLM 适配器（见 §6.2），与 dedup 注入 `embedder` 同一模式。
- **LLM 隔离（CLAUDE.md 架构约束）**：唯一外部副作用 = `llm.complete_json(...)`，封装在 `src/adapters/llm/`。orchestrator 之外的核心（建 prompt / 解析校验 / 约束强制 / 抽取式回退 / 必读门）皆为**纯函数**，注入 `FakeLLMProvider` 离线确定性可测。
- **prompts 运行时加载**：解读 / 今日看点的提示词放 `src/prompts/*.md`，`load_prompt(path)` 运行时读取，不硬编码散落（CLAUDE.md 内容纪律）。

## 4. 数据契约

```python
class Evidence(BaseModel):
    claim: str                           # 关键事实(中文)
    anchor: str                          # 原文锚点; 必须 ∈ item.link ∪ related_links

class InterpretedItem(ScoredItem):       # ScoredItem 的下游演进; 本圈加解读字段
    title: str                           # 中文标题, ≤ title_max_chars(64)
    summary: str                         # 中文摘要, ≤ summary_max_chars(120)
    takeaway: str                        # 对你意味着什么/怎么用; 回退时为 ""
    hot_take: str = ""                   # 锐评 AI 草稿(待人工定稿); 可空
    tags: list[str] = []                 # 恰好 tags_count(3) 个; 回退时为 []
    evidence: list[Evidence] = []        # 证据链; anchor 已过滤为合法锚点
    interpretation_status: str           # "ok" | "extractive_fallback"
    eligible_for_must_read: bool         # 派生(§5.4)

class InterpretResult:
    interpreted_items: list[InterpretedItem]  # 按 score 降序(继承上游序, tie-break 同 §5.5)
    daily_take: str | None               # 今日看点 3-5 句; LLM 失败置 None(不编造)
    input_count: int                     # 入参 items 数
    interpreted_count: int               # status=="ok" 的条数
    fallback_count: int                  # status=="extractive_fallback" 的条数
    is_silent: bool                      # input_count == 0
```

> `interpreted_count + fallback_count == input_count`（每条要么解读成功要么回退，恒不丢条）。

## 5. 算法（确定性 / IO 隔离）

### 5.1 空输入短路

`items == []` → 返回 `InterpretResult(interpreted_items=[], daily_take=None, input_count=0, interpreted_count=0, fallback_count=0, is_silent=True)`，**不调用 LLM**，不抛异常（PRD §3.4 静默）。

### 5.2 单条解读（逐条，按上游 score 降序）

对每个 `ScoredItem`：

1. `build_item_prompt(item, config)` —— 用 `src/prompts/interpret_item.md` 模板 + 注入 `title_en` / `raw_summary` / `source` / `genre` / `link` / `related_links`。提示词要求 LLM **先抽取事实、再成文**（PRD §4.4 生成纪律），输出固定结构 JSON：`{title, summary, takeaway, hot_take, tags: [..], evidence: [{claim, anchor}, ..]}`。
2. `raw = llm.complete_json(prompt, ...)` —— 唯一外部调用。
3. `parse_and_validate(raw, config)` —— JSON 解析 + pydantic 校验字段类型/必填。
4. `enforce_constraints(parsed, item, config)`（纯函数）：
   - `title` 截断到 `title_max_chars`（≤64）；`summary` 截断到 `summary_max_chars`（≤120）。
   - `tags`：数量必须**恰好等于** `tags_count`（3）个；`len(tags) != tags_count`（含为空、不足、超出）⇒ 视为解读不达标，触发回退（§5.3），不强行截断/补造。
   - `evidence`：逐条过滤 `anchor ∈ {item.link} ∪ set(item.related_links)`；非法锚点的 evidence **丢弃**（不编造锚点）。
5. 成功 ⇒ `InterpretedItem(..., interpretation_status="ok")`。

### 5.3 抽取式回退（PRD §3.4 / §4.4「宁可少写不可编造」）

第 2-4 步**任一失败**（网络异常 / 非 JSON / schema 不符 / tags 不达标）⇒ 回退（纯函数 `extractive_fallback(item, config)`）：

- `title = item.title_en`（不翻译，避免编造）；`summary = item.raw_summary` 截断到 `summary_max_chars`（`raw_summary` 为空则 `""`）。
- `takeaway = ""`、`hot_take = ""`、`tags = []`、`evidence = []`。
- `interpretation_status = "extractive_fallback"`。

单条失败用 per-item try/except 隔离，**不影响其它条**（部分降级，全链不阻断）。

### 5.4 必读门（派生 `eligible_for_must_read`，PRD §4.4「无证据不进必读」）

```
eligible_for_must_read = (interpretation_status == "ok")
                         and (len(evidence) >= config.min_evidence)
                         and (takeaway != "")
```

本层只**标记**资格；实际"今日必读 Top3"分组在下游发布层据此筛。

### 5.5 排序与确定性

`interpreted_items` 保持上游 `score` 降序；同分用 `published_at` 升序、再 `link` 升序兜底（与打分层 §5.4 tie-break 一致）。同一输入 + 同一 `FakeLLMProvider` 返回 ⇒ 同字段 / 同顺序 / 同 status（确定性，注入固定 JSON 离线可测）。

### 5.6 今日看点（`daily_take`，PRD §5.2）

对**已解读条目**（优先 `status=="ok"` 的 title/summary）做一次 LLM 调用（`src/prompts/daily_take.md`，要求 3-5 句宏观趋势、无 AI 味）。解析/网络失败 ⇒ `daily_take = None`（不编造）。`items` 非空但全部回退时仍尝试；失败置 None。

## 6. 配置与 provider

### 6.1 `config/interpret.yaml`

```yaml
model: "Qwen/Qwen2.5-72B-Instruct"   # OpenAI 兼容 chat 模型(ModelScope); 可换
temperature: 0.3
max_tokens: 800
timeout_s: 60
title_max_chars: 64                  # PRD §5.5 中文标题 ≤64
summary_max_chars: 120               # PRD §5.5 摘要 ≤120
tags_count: 3                        # PRD §5.5 恰好 3 个
min_evidence: 1                      # 必读门: 至少 1 条证据(§5.4)
item_prompt_path: "src/prompts/interpret_item.md"
daily_prompt_path: "src/prompts/daily_take.md"
```

对应 `InterpretConfig`（dataclass，默认值与上表一致）：`model: str`、`temperature: float`、`max_tokens: int`、`timeout_s: int`、`title_max_chars: int`、`summary_max_chars: int`、`tags_count: int`、`min_evidence: int`、`item_prompt_path: str`、`daily_prompt_path: str`。加载器 `load_interpret_config(path)` 与 `load_dedup_config`/`load_scoring_config` 同风格（缺文件 → 默认值；`.get` per field）。

### 6.2 `LLMProvider` 协议与适配器

```python
class LLMProvider(Protocol):
    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        """Return the model's raw text completion (expected to be JSON).
        Raise to signal a provider/network failure (caller falls back)."""
        ...
```

- 真实适配器 `src/adapters/llm/openai_compat.py`（`OpenAICompatLLM`）：OpenAI 兼容 `/chat/completions`，复用 `MODELSCOPE_API_KEY`、`httpx`。与 `ModelScopeEmbedder` 同构（构造注入 api_key/model/timeout）。
- 测试假体 `tests/fakes.py`：
  - `FakeLLMProvider(responses_by_key)` —— 按 prompt 关键字段（如 `link` 或 `title_en`）返回固定 JSON 字符串；缺键可返回一个默认。
  - `FailingLLMProvider` —— `complete_json` 抛异常（模拟全失败 → 全回退）。

### 6.3 prompts

`src/prompts/interpret_item.md`、`src/prompts/daily_take.md` 为版本化的内容 SOP（CLAUDE.md：运行时加载，不硬编码）。`load_prompt(path) -> str`（`open(encoding="utf-8").read()`）。内容要求对齐 PRD §4.4：分层、可操作性、锐评无 AI 味、术语保留英文、先抽取事实再成文、关键事实带 evidence。

## 7. 错误与回退（非致命，继承 CLAUDE.md/PRD §3.4）

| 情况 | 处理 |
|---|---|
| 入参 `items == []`（上游 `is_silent`） | 返回空 `InterpretResult`（`is_silent=True`），不调 LLM，不抛 |
| 单条 LLM 网络/超时失败 | 该条抽取式回退（§5.3），`status=extractive_fallback`，不影响其它条 |
| LLM 返回非 JSON / schema 不符 | 当作失败 → 该条回退 |
| `tags` 数量不达标 | 视为不达标 → 该条回退（不强行编造 tag） |
| `evidence.anchor` 不在 `link∪related_links` | 丢弃该条 evidence（不编造锚点）；若清空后无 evidence 则 `eligible_for_must_read=False` |
| `raw_summary` 为空且回退 | `summary=""`（宁可少写不可编造） |
| 今日看点 LLM 失败 | `daily_take=None` |
| `--dry-run` | 链路 `collect()→dedup()→score()→interpret()`；产 `InterpretResult` JSON |

## 8. 不变量（golden 测试必须断言）

1. **零编造**：`status=="extractive_fallback"` 的条目 `takeaway==""`、`hot_take==""`、`tags==[]`、`evidence==[]`（绝不编造内容）。
2. **必读门**（PRD #5）：`eligible_for_must_read == (status=="ok" ∧ len(evidence)≥min_evidence ∧ takeaway≠"")`；evidence 为空的条目 `eligible_for_must_read==False`。
3. **证据锚点合法**：每条 `evidence` 的 `anchor ∈ item.link ∪ item.related_links`（非法锚点已被丢弃）。
4. **字段约束**：`status=="ok"` 时 `len(title)≤title_max_chars`、`len(summary)≤summary_max_chars`、`len(tags)==tags_count`。
5. **不丢条**：`interpreted_count + fallback_count == input_count == len(interpreted_items)`。
6. **确定性**：同一输入 + 同一注入 LLM 返回 ⇒ 同字段 / 同顺序 / 同 status / 同 `daily_take`。
7. **排序**：`interpreted_items` 按 `score` 降序（同分 `published_at` 升序、`link` 升序兜底）。
8. 入参 `[]` → 空 `InterpretResult`、`is_silent=True`、`daily_take=None`、**不调用 LLM**、不抛异常。
9. 每个 `InterpretedItem` 继承全部 `ScoredItem`/`NewsItem`/`RawItem` 不变量（score∈[0,100]、breakdown 9 键、cluster_id 非空等）。

## 9. golden 用例（fixtures 驱动，≥6）

> 用**冻结的 ScoredItem fixtures** + 注入 `FakeLLMProvider`（按条返回固定 JSON）/`FailingLLMProvider`，使解读确定、可断言，不依赖网络与真实 LLM。

1. **happy 全字段**：FakeLLM 返回合规 JSON（title/summary/takeaway/hot_take/3 tags/1 evidence 且 anchor==link）→ `status=="ok"`、字段约束满足（不变量 4）、`eligible_for_must_read==True`（不变量 2）。
2. **tags 强制为 3**：FakeLLM 返回 tags 仅 2 个 → 触发回退，`status=="extractive_fallback"`、`tags==[]`（不变量 1）。
3. **LLM 全失败 → 抽取式回退**：注入 `FailingLLMProvider` → 全条 `status=="extractive_fallback"`、零编造（不变量 1）、`title==title_en`、`summary==raw_summary[:120]`、`daily_take is None`。
4. **evidence 空 → 不进必读**：FakeLLM 返回合规但 `evidence==[]` → `eligible_for_must_read==False`（不变量 2）。
5. **空输入 → silent**：`items==[]` → 空 `InterpretResult`、`is_silent=True`、`daily_take is None`、LLM 未被调用（不变量 8）。
6. **非法锚点丢弃 + 确定性**：FakeLLM 返回 evidence anchor 不在 `link∪related_links` → 该 evidence 被丢弃、`eligible_for_must_read==False`（不变量 3）；重复调用结果一致（不变量 6）。

## 10. 测试要求

- **contract**：`load_interpret_config` 加载（缺文件回默认 / 覆盖字段）；`load_prompt` 读取；`InterpretedItem`/`Evidence`/`InterpretResult` schema 校验；`OpenAICompatLLM` 适配器（`respx` mock 一次 happy + 一次失败抛异常）。
- **golden**：用冻结 fixtures + `FakeLLMProvider`/`FailingLLMProvider` 驱动 §9 的 6 个用例，断言 §8 不变量。
- 一律注入 LLM，**不打真实网络、不依赖真实 LLM 输出**；时间用注入的 `ctx.now`。
- 纯函数 `build_item_prompt`/`parse_and_validate`/`enforce_constraints`/`extractive_fallback`/`gate_must_read` 全程离线可测。

## 11. 可观察

- 每条解读后 emit `item_interpreted{link, status, evidence_count}`（复用 dedup/score 的 `emit`）。
- 每条回退 emit `interpret_fallback{link}`。
- 今日看点产出后 emit `daily_take_done{ok: bool}`。
- `interpret()` 结束 emit `interpret_done{input_count, interpreted_count, fallback_count, silent}`，写入 `runs`。

## 12. 验收（对齐 PRD §2.1）

- **#5 解读零幻觉**：可入必读条目带 `takeaway` + `evidence`（锚点合法齐全）；**无证据条目 `eligible_for_must_read==False`**；LLM 失败回退抽取式（golden 断言零编造字段）。
- **#1 端到端**：`collect()→dedup()→score()→interpret()` 可串联，`--dry-run --interpret` 产出 `InterpretResult` JSON，无人工干预。
- **#8 静默正确**：上游静默时本层返回空结果（`is_silent=True`），不调 LLM、不产空数据、不抛异常。
- **#9 可观察**：`interpret_done` 等事件写入 `runs`，可复盘"今天为什么这样解读、哪些条回退了"。
