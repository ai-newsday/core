# Spec — 质量自检层 (Self-check / Quality guardrail)

> 路径：`docs/specs/selfcheck.md`。七层流水线的第 4.5 层（advisor），夹在第 4 层解读与第 5 层审阅之间。
> 实现：`src/pipeline/selfcheck.py`；配置 `config/selfcheck.yaml`；提示词 `src/prompts/selfcheck.md`。
> 上游：第 4 层解读 (`docs/specs/interpret.md`) 产出的 `InterpretResult`。下游：第 5 层审阅 (`docs/specs/review.md`) —— 自检产的 flag 随条目流到人工审阅，人据此留/删/改。
> 对应 PRD §3.6（表格 P2「质量自检 pass（格式锁 + 证据链校验）」）、§4.4（解读层 + 质量护栏：分层 / 可操作性 / 锐评无 AI 味 / 证据链 / 生成纪律）、PRD 概览第 6 条「引入轻量证据链与自检（防 AI 味、防事实不实）」。
> 设计稿：`docs/superpowers/specs/2026-06-16-quality-selfcheck-layer-design.md`。

## 1. 目的

把解读层产出的 `InterpretedItem`，在进人工审阅**之前**过一道**自检**：标出"可能事实不实 / 有 AI 味 / 格式不达标"的条目，让人工审阅时一眼看到风险点。这是 PRD §4.4「自检（防 AI 味、防事实不实）」唯一尚未落地的部分。

**本层是 advisor，不是 gate**：只在条目上附 `quality_flags`，**不 demote、不 drop、不改字段**。人工审阅（第 5 层）是唯一拍板点。等编辑规范真正稳定、且格式问题可自动补救后，再考虑把确定性的格式锁升级为硬 gate（本圈不做）。

## 2. 范围 / 非目标

**做：**

| 能力 | 说明 |
|---|---|
| 格式锁收口（确定性，无 LLM） | `format_lint`：把散在 interpret/review 的长度 / 标签数 / 锚点合法 / 必读门校验，收成一处**具名 lint**，对不达标项产 flag。**不重新强制**（不截断、不回退）——interpret 已做强制，这里只**报告状态**。 |
| 防事实不实（LLM critic，内部一致性） | 检查 `takeaway`/`summary`/`hot_take` 的关键事实能否从**手头已有文本**（`raw_summary` + `title_en` + 各 `evidence.anchor`）推出。**不联网、不抓正文**。抓"原文没说却写了"的编造。 |
| 防 AI 味（LLM critic） | 对 `hot_take`/`summary` 做风格自检：套话 / 空洞无判断 / AI 腔 / 丢了作者 style（对齐 §4.4 编辑规范）。 |
| 范围控制 | LLM critic **只跑 `eligible_for_must_read==True` 的条目**（每期约 7-8 条）。回退条目本就无生成内容，跳过。 |
| 产物 + 留痕 | 在条目上附 `quality_flags`，产 `SelfCheckResult`，写 `runs` 事件，支持 `--dry-run`（CLI `--selfcheck`）。 |

**不做（明确延后）：**

| 不做 | 为什么 / 归属 |
|---|---|
| 联网抓正文做真实事实核查 | 管道今天不抓正文（采集层职责，跨层）；信号源不存在，先做内部一致性。见 §9。 |
| 任何 gate / demote / drop / 改字段 | 本层是 advisor；拍板在审阅层。规范稳定后另起一圈再 gate。 |
| 重新实现长度 / 标签强制 | interpret `build_ok_item` 已强制；这里只收口报告，避免造重复轮子。 |
| 对回退条目跑 LLM | 回退条目 `takeaway/hot_take/tags/evidence` 皆空，无可检之物。 |
| 审阅 UI 上如何展示 flag | 审阅层 / 发布层职责。 |
| 真实 Anthropic 原生接入 | 复用解读层的 OpenAI 兼容适配器（ADR 0001）；critic 可换更便宜模型（§7）。 |

## 3. 接口契约

```python
def self_check(result: InterpretResult, config: SelfCheckConfig,
               ctx: RunContext, llm: LLMProvider) -> SelfCheckResult: ...
```

- **输入**：上游 `InterpretResult`、`SelfCheckConfig`（§7）、`RunContext`（复用 `run_id`/`now`/`logger`）、注入的 `LLMProvider`（与解读层同一协议）。
- **LLM 隔离**：唯一外部副作用 = 对 eligible 条目的 `llm.complete_json(...)`。建 prompt / 解析 / 格式 lint / flag 组装 全是**纯函数**（`format_lint` / `build_critic_prompt` / `parse_critic`），注入 `FakeLLMProvider` 离线确定性可测。
- **prompts 运行时加载**：critic 提示词 `src/prompts/selfcheck.md`，`load_prompt(path)` 读取。
- **不可变流动**：本层产出 annotated 条目（`InterpretedItem` 多了 `quality_flags`），条目身份 / 顺序 / 其它字段一律不动（用 `model_copy(update=...)`）。

## 4. 数据契约（实现见 `src/core/types.py`）

```python
class QualityFlag(BaseModel):
    code: str          # "consistency" | "ai_slop" | "format_lock"
    severity: str      # "warn" | "info"  (advisor 版无 "block")
    field: str         # 命中字段: takeaway|summary|hot_take|tags|evidence|*
    message: str       # 给人看的一句话(中文), ≤ message_max_chars

class InterpretedItem(ScoredItem):       # 既有, 本层新增一字段
    ...
    quality_flags: list[QualityFlag] = []   # 默认空; advisor 标注

@dataclass
class SelfCheckResult:
    interpreted_items: list[InterpretedItem]   # 同上游条目, 已附 quality_flags; 顺序不变
    daily_take: str | None                     # 透传上游, 不改
    checked_count: int                         # 实际跑过 LLM critic 的条数(eligible 数)
    flagged_count: int                         # 至少 1 个 flag 的条数
    flag_count_by_code: dict[str, int]         # {"consistency": n, "ai_slop": n, "format_lock": n}
    llm_error_count: int                       # critic 调用失败 → 该条无语义 flag(不编造)
    is_silent: bool                            # 上游 is_silent 透传
```

> `quality_flags` 加在 `InterpretedItem` 上（不是 sidecar），因此 `ReviewedItem`（extends `InterpretedItem`）**自动继承**，flag 一路流到审阅 / 发布层，无需额外 join。默认空 ⇒ 不破坏既有 interpret/review/publish 测试（byte-identical 兼容）。

## 5. 算法（确定性 / IO 隔离）

### 5.1 空输入 / 静默短路
`result.is_silent or not result.interpreted_items` ⇒ 返回透传 `interpreted_items`/`daily_take` 的全零计数 `SelfCheckResult`，**不调 `load_prompt`、不调 LLM**，不抛。

### 5.2 格式锁 lint（纯函数 `format_lint(item, config) -> list[QualityFlag]`）
逐条检查、**只报告不修改**，命中即产 `code="format_lock"/severity="warn"` flag：
- `len(title) > title_max_chars` 或 `len(summary) > summary_max_chars`。
- `interpretation_status=="ok"` 且 `len(tags) != tags_count`。
- 任一 `evidence.anchor ∉ {item.link} ∪ related_links`（非法锚点）。
- `eligible_for_must_read==True` 但 `len(evidence) < min_evidence` 或 `takeaway==""`（必读门自洽性）。
> 正常流水线下这些大多为空——价值在于**单点具名复核 + 可观察**，任何上游 drift 立刻显形。

### 5.3 LLM critic（`check_item` + `parse_critic`，仅 `eligible_for_must_read==True` 条目）
对每个 eligible 条目：
1. `build_critic_prompt(item, template)` —— 注入 `title`/`summary`/`takeaway`/`hot_take`/`title_en`/`raw_summary`/`evidence`。提示词要求 LLM：(a) 内部一致性判定（**不得引入外部知识**）；(b) AI 味判定。输出固定结构 JSON：`{"consistency": [{"field","message"}...], "ai_slop": [{"field","message"}...]}`。
2. `raw = llm.complete_json(prompt, temperature=config.temperature, max_tokens=config.max_tokens)` —— 唯一外部调用。
3. `parse_critic(raw, config)`（纯函数）：JSON 解析（失败抛 `ValueError`）+ 逐项裁剪 `message` 到 `message_max_chars`，`field` 落白名单否则归 `"*"`，每类最多 `max_flags_per_item` 个。`consistency→severity=warn`，`ai_slop→severity=info`。
4. 成功 ⇒ 追加这些 flag。

### 5.4 critic 失败 = 不编造（生成纪律）
第 1-3 步任一失败（网络 / 非 JSON / schema 不符）⇒ 该条**不产任何语义 flag**，`llm_error_count += 1`，emit `selfcheck_error{link, error_type}`。**绝不**因 critic 失败就标"有问题"或"没问题"。格式锁 flag 不受影响（纯函数，先于 critic 跑）。

### 5.5 合并与确定性
每条 `quality_flags = format_lint(...) + critic_flags(...)`（先 format_lock，后 consistency，后 ai_slop）。条目顺序 / daily_take / 其它字段一律透传不变（`model_copy`）。同一输入 + 同一 `FakeLLMProvider` 返回 ⇒ 同 flag / 同顺序 / 同计数。

## 6. 错误与回退（非致命）

| 情况 | 处理 |
|---|---|
| 上游 `is_silent` 或空 | 透传空结果，不调 `load_prompt`/LLM，不抛（§5.1） |
| 单条 critic 网络/超时/非 JSON | 该条无语义 flag，`llm_error_count++`，不影响其它条与格式 lint |
| critic 返回非法 `field` | 归一到 `"*"`，不丢该 flag |
| critic 某类 flag 超 `max_flags_per_item` | 截断到上限（防爆 token / 防刷屏） |
| `--dry-run --selfcheck` | 链路 `collect→dedup→score→interpret→self_check`，产 `SelfCheckResult` JSON |

## 7. 配置与 provider

### 7.1 `config/selfcheck.yaml`
```yaml
model: "Qwen/Qwen2.5-7B-Instruct"    # critic 用便宜模型; 可换
temperature: 0.0                     # 判定型任务, 求稳定
max_tokens: 600
timeout_s: 60
title_max_chars: 64                  # 与 interpret 对齐
summary_max_chars: 120
tags_count: 3
min_evidence: 1
message_max_chars: 120
max_flags_per_item: 3                # 每类(consistency/ai_slop)各自上限
prompt_path: "src/prompts/selfcheck.md"
```
对应 `SelfCheckConfig`（dataclass，默认值同上）。`load_selfcheck_config(path)`（`src/core/config.py`）与 `load_interpret_config` 同风格（缺文件→默认；`.get` per field）。

### 7.2 provider
复用 interpret 的 `LLMProvider` 协议与 `OpenAICompatLLM` 适配器、`FakeLLMProvider`/`FailingLLMProvider` 测试假体。critic 可配独立（更便宜）模型，互不影响。

## 8. 不变量（golden 测试断言，`tests/golden/test_selfcheck.py`）

1. **advisor 不改条目**：输出 `interpreted_items` 与输入除 `quality_flags` 外**逐字段相等**、**顺序相等**、条数相等。
2. **critic 范围**：`checked_count == len([i for i in items if i.eligible_for_must_read])`；非 eligible 条目**不产** consistency/ai_slop flag（可有 format_lock）。
3. **不编造**：critic 调用失败的条目**无** consistency/ai_slop flag；`llm_error_count` 计数正确。
4. **格式 lint 纯函数**：不调 LLM 即可对冻结 fixtures 断言 format_lock flag。
5. **flag 裁剪**：每个 `message` ≤ `message_max_chars`；每类 flag ≤ `max_flags_per_item`；非法 `field` 归 `"*"`。
6. **确定性**：同输入 + 同注入 LLM ⇒ 同 flag 内容 / 顺序 / `flag_count_by_code`。
7. **静默**：上游 `is_silent` 或空 ⇒ 空结果、`is_silent` 透传、`checked_count==0`、**不调用 LLM**、不抛。
8. **透传**：`daily_take` 原样透传不变。
9. **兼容**：`quality_flags` 默认 `[]`，既有 interpret/review/publish 测试不受影响。

## 9. 信号源校验（challenge-premise 记录）

- **存在的信号**：`InterpretedItem` 已带结构化 `takeaway`/`hot_take`/`summary`/`tags`/`evidence`(锚点合法) + `eligible_for_must_read` —— 格式锁、AI 味、内部一致性三项检查都有可检之物。✅
- **不存在的信号**：**原文正文**。interpret §2 明确不抓正文，管道只有 `raw_summary`(常被截断) + 标题。故"claim 是否被真实原文支持"的强事实核查**无信号源**，本层降级为**内部一致性**（claim vs 手头可见文本），不联网。强核查留待采集层补"抓正文"信号后另开一圈。

## 10. 测试要求（实现见 `tests/`）

- **contract**：
  - `tests/contract/test_selfcheck_types.py` —— `QualityFlag`/`SelfCheckConfig`/`SelfCheckResult` schema + `quality_flags` 默认 `[]` 兼容。
  - `tests/contract/test_selfcheck_config.py` —— `load_selfcheck_config`（缺文件回默认 / 覆盖字段）。
  - `tests/contract/test_selfcheck_format_lint.py` —— `format_lint` 纯函数各分支。
  - `tests/contract/test_selfcheck_critic_parse.py` —— `build_critic_prompt` / `parse_critic`（映射 / 裁剪 / 封顶 / 非法 field / 坏 JSON）。
  - `tests/contract/test_selfcheck_assets.py` —— `config/selfcheck.yaml` + `src/prompts/selfcheck.md` 占位符 + 两键结构。
  - `tests/contract/test_selfcheck_cli.py` —— `run_dry_selfcheck` 入口与签名。
- **golden**：`tests/golden/test_selfcheck.py` —— 冻结 `InterpretResult` fixtures + `FakeLLMProvider`/`FailingLLMProvider` 驱动 §8 不变量。
- 一律注入 LLM，**不打真实网络**；时间用 `ctx.now`。

## 11. 可观察

- `selfcheck_start{run_id, input_count}`（开始）。
- 每条 emit `item_self_checked{link, flag_codes: [...], n_flags}`。
- critic 失败 emit `selfcheck_error{link, error_type}`。
- `self_check()` 结束 emit `selfcheck_done{checked_count, flagged_count, flag_count_by_code, llm_error_count, silent}`，写入 `runs`。

## 12. 验收（对齐 PRD §2.1）

- **零编造**：critic 失败的条目无语义 flag（golden 断言）。
- **advisor 纯净**：条目除 `quality_flags` 外逐字段不变（不变量 1），下游 review/publish 行为不被本层改变。
- **端到端**：`collect→dedup→score→interpret→self_check` 可串联，`--dry-run --selfcheck` 产 `SelfCheckResult` JSON，无人工干预。
- **静默正确**：上游静默时空结果、不调 LLM、不抛。
- **可观察**：`selfcheck_done` 写入 `runs`。
