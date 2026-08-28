# Content Certainty + Title Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `interpret_item.md` stop rewarding hedgy/self-doubting content with a full score, stop inventing action verbs ("发布") the source text doesn't support, and make paper titles front-load the method/model name as a hook — all via prompt constraints plus one scoring hook.

**Architecture:** Add a new `content_certain: bool` field to the `interpret_item.md` JSON output. When false, `interpret.py::build_ok_item` applies a fixed score penalty (mirrors the existing `firehose_penalty` pattern) and records it in `score_breakdown`. The penalty value lives on `ScoringConfig` (not `InterpretConfig`) for stylistic consistency with `firehose_penalty`, so it must be threaded from the caller (`cli.py`, which already loads both configs) through `interpret()` → `interpret_item()` → `build_ok_item()`. The title action-word guard and the paper-title hook rule are pure prompt-text additions with no code changes — `apply_quota()` in `publish.py::build_report()` already re-sorts by `-score` after human keep decisions, so a lowered score naturally loses ties for a configured slot without touching publish code.

**Tech Stack:** Python 3.12, pydantic dataclasses (`src/core/types.py`), pytest.

## Global Constraints

- Fixed penalty, not a new weighted scoring dimension (already confirmed with the user; corresponds to KANBAN #69's "not a simple new dimension" concern).
- Score never goes below 0 (existing `max(0, ...)` clamp pattern used by other penalties).
- Only affects new items going forward — no backfill/rescoring of already-published content.
- `extractive_fallback` path is unaffected (no LLM-generated `content_certain` value exists there, so no penalty logic runs).
- Missing/absent `content_certain` field in LLM output must default to `True` (no penalty) — backward compatible with any LLM response that omits it.

---

## File Structure

- Modify `src/core/types.py` — add `ScoringConfig.uncertain_content_penalty: float = -15.0`.
- Modify `src/core/config.py` — `load_scoring_config` reads `penalty.uncertain_content` (nested, same block as `same_source`/`firehose`).
- Modify `config/scoring.yaml` — add `uncertain_content: -15` to the existing `penalty:` block.
- Modify `src/prompts/interpret_item.md` — add `content_certain` field + rule text, title action-word guard, paper-title hook rule.
- Modify `src/pipeline/interpret.py` — thread `uncertain_content_penalty: float` through `interpret()` → `interpret_item()` → `build_ok_item()`; apply penalty + write `score_breakdown["内容确定性"]`.
- Modify `src/cli.py` — pass `scfg.uncertain_content_penalty` into every `interpret(...)` call site (5 call sites: `_dry_run_prefix` and `_collect_and_interpret` inside `run_tick`).
- Modify `tests/contract/test_scoring_config.py` — loader test for the new field.
- Modify `tests/contract/test_prompts.py` — prompt content assertions for the three new rules.
- Modify `tests/contract/test_interpret_unit.py` — `build_ok_item`/`interpret_item` penalty behavior tests.

---

### Task 1: `ScoringConfig.uncertain_content_penalty` + config loader + production YAML

**Files:**
- Modify: `src/core/types.py:174-175` (inside `ScoringConfig`, next to `firehose_penalty`)
- Modify: `src/core/config.py:45-75` (`load_scoring_config`)
- Modify: `config/scoring.yaml:42-44` (`penalty:` block)
- Test: `tests/contract/test_scoring_config.py`

**Interfaces:**
- Produces: `ScoringConfig.uncertain_content_penalty: float` (default `-15.0`), read by `src/core/config.py::load_scoring_config` from YAML key `penalty.uncertain_content`.

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_scoring_config.py`:

```python
def test_loads_uncertain_content_penalty(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text("penalty:\n  uncertain_content: -20\n", encoding="utf-8")
    c = load_scoring_config(str(p))
    assert c.uncertain_content_penalty == -20


def test_uncertain_content_penalty_default():
    assert ScoringConfig().uncertain_content_penalty == -15.0


def test_production_config_has_uncertain_content_penalty():
    c = load_scoring_config("config/scoring.yaml")
    assert c.uncertain_content_penalty == -15.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_scoring_config.py -v -k uncertain_content`
Expected: FAIL — `AttributeError: 'ScoringConfig' object has no attribute 'uncertain_content_penalty'`

- [ ] **Step 3: Add the field to `ScoringConfig`**

In `src/core/types.py`, inside `class ScoringConfig` right after the `firehose_penalty` line:

```python
    firehose_penalty: float = -20.0  # 信号闸: 个人+零人气的 model/writeup 扣分(压 firehose 噪声)
    uncertain_content_penalty: float = -15.0  # body 自我保留/信息稀薄时的固定扣分(不是加权维度)
```

- [ ] **Step 4: Wire the loader**

In `src/core/config.py::load_scoring_config`, add to the `penalty` block read and the returned `ScoringConfig(...)` call:

```python
        firehose_penalty=penalty.get("firehose", d.firehose_penalty),
        uncertain_content_penalty=penalty.get("uncertain_content", d.uncertain_content_penalty),
```

(Insert directly after the existing `firehose_penalty=...` line inside the `return ScoringConfig(...)` call.)

- [ ] **Step 5: Add to production YAML**

In `config/scoring.yaml`, change:

```yaml
penalty:
  same_source: -5                 # 同源第2+条各扣 (spec §5.3)
  firehose: -20                   # 信号闸: 个人+零人气的 model/writeup 扣分(压 gemma 类噪声)
```

to:

```yaml
penalty:
  same_source: -5                 # 同源第2+条各扣 (spec §5.3)
  firehose: -20                   # 信号闸: 个人+零人气的 model/writeup 扣分(压 gemma 类噪声)
  uncertain_content: -15          # body 自我保留("未披露/需后续验证")或信息稀薄时扣分, 不再无区分拿满分
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_scoring_config.py -v -k uncertain_content`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add src/core/types.py src/core/config.py config/scoring.yaml tests/contract/test_scoring_config.py
git commit -m "feat(scoring): add uncertain_content_penalty config field"
```

---

### Task 2: Prompt rules — `content_certain`, title action-word guard, paper-title hook

**Files:**
- Modify: `src/prompts/interpret_item.md`
- Test: `tests/contract/test_prompts.py`

**Interfaces:**
- Produces: `interpret_item.md` JSON schema gains `"content_certain": true` as a top-level output key, consumed by Task 3's `build_ok_item`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_prompts.py`:

```python
def test_interpret_prompt_has_content_certain_field():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "content_certain" in t
    assert '"content_certain"' in t


def test_interpret_prompt_has_title_action_word_guard():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "动作词" in t
    assert "依据" in t


def test_interpret_prompt_has_paper_title_hook_rule():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "paper" in t
    assert "方法名" in t or "模型名" in t
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_prompts.py -v -k "content_certain or action_word or paper_title_hook"`
Expected: FAIL — 3 assertion errors (strings not found)

- [ ] **Step 3: Edit the prompt**

Replace the full contents of `src/prompts/interpret_item.md` with:

```markdown
你是中文 AI 资讯日报的资深编辑。基于给定的英文条目信息，产出**结构化 JSON**解读。

硬约束（必须遵守）：
- 先抽取事实、再成文；只依据下方提供的信息，**不得编造**任何事实或链接。
- `title`：中文钩子标题，简洁可扫读，带数字/反差更佳，≤64 字；模型名/公司名/技术名保留英文原文。`title` 里的动作词（发布/推出/开源/上线等）必须能在原文摘要或相关链接里找到依据；找不到依据时改用中性表述（如"更新"/"介绍"/"谈"），不得凭"这类新闻通常怎么写"去猜一个动作词。若类型（genre）为 paper：标题必须把论文提出的方法名/模型名放在最前面当钩子（参考"0.22B 反超 11.9B：Moebius 把 Image Inpainting 模型压到 2% 体量"这类结构：数字/反差 + 方法名 + 效果），不要用"研究者提出新方法"这类不带信息量的开头。
- `body`：一段顺读中文正文，≤180 字。先讲清事实，再落到"对从业者意味着什么、能怎么用"，可选用一句克制的判断收尾。不要分点、不要"一句话/对你/锐评"之类标签，不堆形容词，不用 emoji。
- `tags`：恰好 3 个，每个以 # 开头。
- `evidence`：关键事实 → 原文锚点；anchor 只能取自下方 link 或 related_links，不得编造；无法给出有锚点的事实时返回空数组。
- `relevant`：布尔值。该条目**既与 AI/机器学习相关、又有可写的真实内容**时为 true；若**与 AI 无关**（例如只是恰好含 "model/agent" 等词的非 AI 文章），或**没有实质内容**（原文缺失、无法概述），则为 false。
- `content_certain`：布尔值。若 `body` 里出现"未披露/尚不明确/需后续验证/具体细节未知"这类自我保留表述，或原文摘要本身信息稀薄、只是预告性质，为 false；信息扎实、有具体数据/机制支撑时为 true。
- 机构归属：Hugging Face / GitHub 等只是**托管平台**，不是论文/项目的作者机构；不得写"Hugging Face 发布论文/提出方法"这类表述。原文摘要里没有给出真实机构（学校/公司/实验室）信息时，就不要点名机构，只说"研究者/团队"或直接讲方法本身。下方"来源"字段是内部采集渠道分类，**不等于**真实发布方；真实发布方以原文摘要为准（如摘要以 `@handle:` 开头，那就是真实作者账号）。判断不出真实发布方时，不要凭"来源"字段的名字猜/编一个公司名，改用"该账号/团队"等中性表述。

只输出 JSON，结构如下（不要额外解释）：
{"title": "...", "body": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}], "relevant": true, "content_certain": true}

条目信息：
- 英文标题: {{title_en}}
- 来源: {{source}}（类型 {{genre}}）
- 主链接: {{link}}
- 相关链接:
{{related_links}}
- 原文摘要: {{raw_summary}}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_prompts.py -v`
Expected: PASS (all, including pre-existing prompt tests — `test_interpret_prompt_uses_body_schema`, `test_interpret_prompt_has_relevant_field`, `test_interpret_prompt_forbids_misattributing_hosting_platform` must still pass unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/prompts/interpret_item.md tests/contract/test_prompts.py
git commit -m "feat(interpret): add content_certain field + title action-word/hook guards to prompt"
```

---

### Task 3: `build_ok_item` consumes `content_certain` and applies the penalty

**Files:**
- Modify: `src/pipeline/interpret.py:92-256` (`build_ok_item`, `interpret_item`, `interpret`)
- Test: `tests/contract/test_interpret_unit.py`

**Interfaces:**
- Consumes: `ScoringConfig.uncertain_content_penalty` (Task 1), passed in as a plain `float` argument (not a `ScoringConfig` object — `interpret.py` must not import `ScoringConfig` to avoid a needless cross-module coupling; the caller extracts the float).
- Produces:
  - `build_ok_item(parsed: dict, item: ScoredItem, config: InterpretConfig, uncertain_content_penalty: float = -15.0) -> InterpretedItem`
  - `interpret_item(item: ScoredItem, item_template: str, config: InterpretConfig, llm, logger=None, uncertain_content_penalty: float = -15.0) -> InterpretedItem`
  - `interpret(items: list[ScoredItem], config: InterpretConfig, ctx: RunContext, llm, uncertain_content_penalty: float = -15.0) -> InterpretResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_interpret_unit.py`:

```python
def test_build_ok_item_content_certain_false_applies_penalty():
    it = _scored(score=80, score_breakdown={"机构影响力": 80.0})
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
        "content_certain": False,
    }
    out = build_ok_item(parsed, it, InterpretConfig(), uncertain_content_penalty=-15.0)
    assert out.score == 65
    assert out.score_breakdown["内容确定性"] == -15.0


def test_build_ok_item_content_certain_false_score_floors_at_zero():
    it = _scored(score=5, score_breakdown={"机构影响力": 5.0})
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
        "content_certain": False,
    }
    out = build_ok_item(parsed, it, InterpretConfig(), uncertain_content_penalty=-15.0)
    assert out.score == 0


def test_build_ok_item_content_certain_true_no_penalty():
    it = _scored(score=80, score_breakdown={"机构影响力": 80.0})
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
        "content_certain": True,
    }
    out = build_ok_item(parsed, it, InterpretConfig(), uncertain_content_penalty=-15.0)
    assert out.score == 80
    assert "内容确定性" not in out.score_breakdown


def test_build_ok_item_content_certain_missing_defaults_no_penalty():
    it = _scored(score=80, score_breakdown={"机构影响力": 80.0})
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.score == 80
    assert "内容确定性" not in out.score_breakdown


def test_interpret_item_threads_uncertain_content_penalty():
    it = _scored(score=80, score_breakdown={"机构影响力": 80.0})
    tpl = load_prompt("src/prompts/interpret_item.md")
    json_body = json.dumps(
        {
            "title": "t",
            "body": "s。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
            "content_certain": False,
        }
    )
    llm = FakeLLMProvider({"https://hf.co/glm5": json_body})
    out = interpret_item(it, tpl, InterpretConfig(), llm, uncertain_content_penalty=-15.0)
    assert out.score == 65


def test_extractive_fallback_unaffected_by_content_certain():
    it = _scored(score=80, score_breakdown={"机构影响力": 80.0})
    out = extractive_fallback(it, InterpretConfig())
    assert out.score == 80  # no penalty logic runs on the fallback path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v -k "content_certain or threads_uncertain"`
Expected: FAIL — `TypeError: build_ok_item() got an unexpected keyword argument 'uncertain_content_penalty'`

- [ ] **Step 3: Implement in `src/pipeline/interpret.py`**

Change `build_ok_item`:

```python
def build_ok_item(
    parsed: dict,
    item: ScoredItem,
    config: InterpretConfig,
    uncertain_content_penalty: float = -15.0,
) -> InterpretedItem:
    """Enforce field constraints (spec §5.2) and build an 'ok' InterpretedItem.
    Raises ValueError if tags count != config.tags_count (caller falls back)."""
    tags = parsed.get("tags")
    if not isinstance(tags, list) or len(tags) != config.tags_count:
        raise ValueError("tags count not met")
    title = str(parsed.get("title", ""))[: config.title_max_chars]
    body = _trim_to_sentence(str(parsed.get("body", "")), config.body_max_chars)
    relevant = bool(parsed.get("relevant", True))
    evidence = _filter_evidence(parsed.get("evidence"), item)
    eligible = bool(body) and len(evidence) >= config.min_evidence
    score = item.score
    breakdown = dict(item.score_breakdown)
    if not parsed.get("content_certain", True):
        score = max(0, score + int(uncertain_content_penalty))
        breakdown["内容确定性"] = uncertain_content_penalty
    return InterpretedItem(
        **{**item.model_dump(), "score": score, "score_breakdown": breakdown},
        title=title,
        body=body,
        tags=[str(t) for t in tags],
        evidence=evidence,
        interpretation_status="ok",
        eligible_for_must_read=eligible,
        relevant=relevant,
    )
```

Change `interpret_item` signature and its call to `build_ok_item`:

```python
def interpret_item(
    item: ScoredItem,
    item_template: str,
    config: InterpretConfig,
    llm,
    logger=None,
    uncertain_content_penalty: float = -15.0,
) -> InterpretedItem:
    """One item: prompt -> LLM chain (each with parse validation) -> enforce.

    Uses ``complete_json`` with a validator so parse failure counts as that
    model failing, letting the remaining models try. Any final failure -> extractive fallback (spec §5.2/§5.3).
    Optional `logger` enables an `interpret_error` emit before fallback."""
    parsed_holder: dict = {}

    def _validate(raw: str) -> None:
        parsed_holder["parsed"] = parse_and_validate(raw)

    try:
        prompt = build_item_prompt(item, item_template, config)
        llm.complete_json(
            prompt,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            validator=_validate,
        )
        parsed = parsed_holder["parsed"]
        return build_ok_item(parsed, item, config, uncertain_content_penalty)
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "interpret_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )
        return extractive_fallback(item, config, fallback_reason=type(e).__name__)
```

Change `interpret` signature and its loop call to `interpret_item`:

```python
def interpret(
    items: list[ScoredItem],
    config: InterpretConfig,
    ctx: RunContext,
    llm,
    uncertain_content_penalty: float = -15.0,
) -> InterpretResult:
    """Orchestrate per-item interpretation + daily take (spec §3, §5, §11).
    Only side effect is the injected llm; everything else is pure/testable."""
    emit(ctx.logger, "interpret_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(
            ctx.logger,
            "interpret_done",
            input_count=0,
            interpreted_count=0,
            fallback_count=0,
            silent=True,
        )
        return InterpretResult(
            interpreted_items=[],
            daily_take=None,
            input_count=0,
            interpreted_count=0,
            fallback_count=0,
            is_silent=True,
        )

    item_tpl = load_prompt(config.item_prompt_path)
    out: list[InterpretedItem] = []
    for it in items:
        res = interpret_item(
            it, item_tpl, config, llm, logger=ctx.logger,
            uncertain_content_penalty=uncertain_content_penalty,
        )
        emit(
            ctx.logger,
            "item_interpreted",
            link=res.link,
            status=res.interpretation_status,
            evidence_count=len(res.evidence),
        )
        if res.interpretation_status == "extractive_fallback":
            emit(ctx.logger, "interpret_fallback", link=res.link)
        out.append(res)

    daily_tpl = load_prompt(config.daily_prompt_path)
    daily = generate_daily_take(out, daily_tpl, config, llm, logger=ctx.logger)
    emit(ctx.logger, "daily_take_done", ok=daily is not None)

    interpreted_count = sum(1 for r in out if r.interpretation_status == "ok")
    fallback_count = len(out) - interpreted_count
    emit(
        ctx.logger,
        "interpret_done",
        input_count=len(items),
        interpreted_count=interpreted_count,
        fallback_count=fallback_count,
        silent=False,
    )
    return InterpretResult(
        interpreted_items=out,
        daily_take=daily,
        input_count=len(items),
        interpreted_count=interpreted_count,
        fallback_count=fallback_count,
        is_silent=False,
    )
```

Note: `score = max(0, score + int(uncertain_content_penalty))` — `int()` because `ScoredItem.score` is constrained `Field(ge=0, le=100)` (an `int`); `score_breakdown` stores the raw float penalty for display/debugging as other breakdown entries do.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: PASS (all — including every pre-existing test in the file, since all new parameters have defaults)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py
git commit -m "feat(interpret): apply uncertain_content_penalty to score when content_certain=false"
```

---

### Task 4: Wire `scfg.uncertain_content_penalty` into every `interpret()` call site in `src/cli.py`

**Files:**
- Modify: `src/cli.py:179-185` (inside `_dry_run_prefix`)
- Modify: `src/cli.py:458-464` (inside `run_tick`'s `_collect_and_interpret`)
- Test: `tests/contract/test_cli.py` (regression only — behavior already covered at the unit level in Task 3)

**Interfaces:**
- Consumes: `src/pipeline/interpret.py::interpret(..., uncertain_content_penalty: float = -15.0)` (Task 3).

- [ ] **Step 1: Edit `_dry_run_prefix`**

In `src/cli.py`, inside `_dry_run_prefix`, change:

```python
    if want >= _STAGES.index("interpret"):
        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        ires = interpret(sres.selected_items, icfg, ctx, llm)
```

to:

```python
    if want >= _STAGES.index("interpret"):
        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        scfg_for_penalty = load_scoring_config("config/scoring.yaml")
        ires = interpret(
            sres.selected_items, icfg, ctx, llm,
            uncertain_content_penalty=scfg_for_penalty.uncertain_content_penalty,
        )
```

(A fresh `load_scoring_config` call is used here rather than reusing the `scfg` from the `score` block above, because that `scfg` variable only exists inside the `if want >= _STAGES.index("score")` branch and dry-run callers can invoke `_dry_run_prefix` with `stop_at="interpret"` — which always runs the score branch first since `_STAGES` is ordered, so `scfg` *would* be in scope by the time this code runs. Reuse it directly instead — see Step 1 revision below.)

- [ ] **Step 1 (revised): Reuse the already-loaded `scfg`**

Since `want >= _STAGES.index("interpret")` implies `want >= _STAGES.index("score")` (stages are strictly ordered: collect < dedup < score < interpret), the `scfg` loaded in the score branch is already in scope. Use it directly instead of reloading:

```python
    if want >= _STAGES.index("interpret"):
        icfg = load_interpret_config("config/interpret.yaml")
        if llm is None:
            llm = _make_llm(icfg)
        ires = interpret(
            sres.selected_items, icfg, ctx, llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
```

- [ ] **Step 2: Edit `run_tick`'s `_collect_and_interpret`**

In `src/cli.py`, inside `run_tick`'s nested `_collect_and_interpret`, change:

```python
        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(sres.selected_items, icfg, ctx, _llm)
        return ires
```

to:

```python
        icfg = load_interpret_config("config/interpret.yaml")
        _llm = llm or _make_llm(icfg)
        ires = interpret(
            sres.selected_items, icfg, ctx, _llm,
            uncertain_content_penalty=scfg.uncertain_content_penalty,
        )
        return ires
```

(`scfg` is already loaded a few lines above in the same function, at `scfg = load_scoring_config("config/scoring.yaml")`.)

- [ ] **Step 3: Run the full test suite to check for regressions**

Run: `uv run pytest tests/ -x -q`
Expected: PASS (all tests green; no signature-mismatch failures since every new parameter has a default)

- [ ] **Step 4: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): wire uncertain_content_penalty from ScoringConfig into interpret() calls"
```

---

## Self-Review

**Spec coverage:**
- ✅ §1 内容确定性 → 固定扣分: Task 1 (config), Task 2 (prompt field), Task 3 (scoring logic), Task 4 (wiring).
- ✅ §2 标题动作词护栏: Task 2 (prompt rule only, no code).
- ✅ §3 hf-papers 标题前置钩子: Task 2 (prompt rule only, no code, conditioned via prose referencing `{{genre}}` already in the template).
- ✅ 不新增 genre 级别权重维度: confirmed — `uncertain_content_penalty` is a flat scalar, not added to `genre_value`.
- ✅ 不回溯重新打分: confirmed — the penalty only applies inside `build_ok_item`, which only runs on freshly-interpreted items, never touches persisted/published data.
- ✅ `extractive_fallback` 不受影响: explicit test in Task 3.
- ✅ 向后兼容 (missing field defaults true): explicit test in Task 3.

**Placeholder scan:** none — every step has literal code/text.

**Type consistency:** `interpret()` → `interpret_item()` → `build_ok_item()` all use the same parameter name and type (`uncertain_content_penalty: float`, default `-15.0`), matching `ScoringConfig.uncertain_content_penalty`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-28-content-certainty-and-title-hooks.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
