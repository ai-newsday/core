# Quality Self-check Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an advisor-only self-check layer (pipeline step 4.5) that runs between interpret and review, attaching `quality_flags` to must-read-eligible items without gating.

**Architecture:** A new pure-core pipeline module `src/pipeline/selfcheck.py`. Deterministic `format_lint` runs on all `ok` items; an injected LLM critic runs only on `eligible_for_must_read` items for internal-consistency + anti-AI-slop. Failures produce no flag (no fabrication). Output is an annotated `InterpretResult`-shaped `SelfCheckResult`; items are passed through unchanged except for the new `quality_flags` field. Mirrors the interpret layer's injection + pure-function + config-driven conventions exactly.

**Tech Stack:** Python 3.12, pydantic (BaseModel), dataclasses, pyyaml, pytest. Reuses the existing `LLMProvider` protocol, `FakeLLMProvider`/`FailingLLMProvider` test fakes, and the `emit` observability helper.

Reference spec: `docs/superpowers/specs/2026-06-16-quality-selfcheck-layer-design.md`.

---

### Task 1: Data contract types

**Files:**
- Modify: `src/core/types.py` (add `QualityFlag`, extend `InterpretedItem`, add `SelfCheckConfig`, `SelfCheckResult`)
- Test: `tests/contract/test_selfcheck_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_types.py
from src.core.types import (
    InterpretedItem,
    QualityFlag,
    ScoredItem,
    SelfCheckConfig,
    SelfCheckResult,
    SourceType,
)
from datetime import datetime, timezone

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _interpreted(**over):
    base = dict(
        title_en="X", link="https://a/1", source="s", source_type=SourceType.MODEL,
        published_at=NOW, raw_summary="r", cluster_id="c", related_links=[],
        score=80.0, score_breakdown={"机构影响力": 80.0}, is_explore=False,
        title="标题", summary="摘要", takeaway="用法", hot_take="锐评",
        tags=["#a", "#b", "#c"], evidence=[], interpretation_status="ok",
        eligible_for_must_read=True,
    )
    base.update(over)
    return InterpretedItem(**base)


def test_quality_flag_schema():
    f = QualityFlag(code="ai_slop", severity="info", field="hot_take", message="太空洞")
    assert f.code == "ai_slop" and f.severity == "info"


def test_interpreted_item_quality_flags_defaults_empty():
    item = _interpreted()
    assert item.quality_flags == []  # new field, default empty -> backward compatible


def test_selfcheck_config_defaults():
    c = SelfCheckConfig()
    assert c.message_max_chars == 120 and c.max_flags_per_item == 3
    assert c.prompt_path == "src/prompts/selfcheck.md"


def test_selfcheck_result_shape():
    item = _interpreted(quality_flags=[QualityFlag(code="consistency", severity="warn", field="takeaway", message="原文没说")])
    res = SelfCheckResult(
        interpreted_items=[item], daily_take="看点", checked_count=1,
        flagged_count=1, flag_count_by_code={"consistency": 1},
        llm_error_count=0, is_silent=False,
    )
    assert res.flagged_count == 1 and res.flag_count_by_code["consistency"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_types.py -v`
Expected: FAIL with `ImportError: cannot import name 'QualityFlag'`

- [ ] **Step 3: Write minimal implementation**

In `src/core/types.py`, add `QualityFlag` right before the existing `class InterpretedItem` (after `class Evidence`):

```python
class QualityFlag(BaseModel):
    code: str  # "consistency" | "ai_slop" | "format_lock"
    severity: str  # "warn" | "info"  (advisor 版无 "block")
    field: str  # 命中字段: takeaway|summary|hot_take|tags|evidence|*
    message: str = Field(min_length=1)  # 给人看的一句话(中文)
```

Add one line inside `class InterpretedItem`, after `eligible_for_must_read: bool`:

```python
    quality_flags: list[QualityFlag] = Field(default_factory=list)  # advisor 标注; 默认空
```

Add the config + result near `class InterpretResult` (after it):

```python
@dataclass
class SelfCheckConfig:
    model: str = "Qwen/Qwen2.5-7B-Instruct"
    temperature: float = 0.0
    max_tokens: int = 600
    timeout_s: int = 60
    title_max_chars: int = 64
    summary_max_chars: int = 120
    tags_count: int = 3
    min_evidence: int = 1
    message_max_chars: int = 120
    max_flags_per_item: int = 3
    prompt_path: str = "src/prompts/selfcheck.md"


@dataclass
class SelfCheckResult:
    interpreted_items: list[InterpretedItem]
    daily_take: str | None
    checked_count: int
    flagged_count: int
    flag_count_by_code: dict[str, int]
    llm_error_count: int
    is_silent: bool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_types.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the existing interpret/review suites to confirm the new field is backward compatible**

Run: `pytest tests/contract/test_interpret_types.py tests/golden/test_interpret.py -q`
Expected: PASS (no failures — `quality_flags` defaults to `[]`)

- [ ] **Step 6: Commit**

```bash
git add src/core/types.py tests/contract/test_selfcheck_types.py
git commit -m "feat(selfcheck): data contract (QualityFlag, quality_flags, SelfCheckConfig/Result)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Config loader

**Files:**
- Modify: `src/core/config.py` (add `load_selfcheck_config`)
- Test: `tests/contract/test_selfcheck_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_config.py
from src.core.config import load_selfcheck_config


def test_missing_file_returns_defaults():
    cfg = load_selfcheck_config("config/does-not-exist.yaml")
    assert cfg.temperature == 0.0 and cfg.max_flags_per_item == 3


def test_overrides_from_yaml(tmp_path):
    p = tmp_path / "selfcheck.yaml"
    p.write_text(
        "model: M\ntemperature: 0.2\nmax_flags_per_item: 5\nmessage_max_chars: 80\n",
        encoding="utf-8",
    )
    cfg = load_selfcheck_config(str(p))
    assert cfg.model == "M" and cfg.temperature == 0.2
    assert cfg.max_flags_per_item == 5 and cfg.message_max_chars == 80
    assert cfg.tags_count == 3  # untouched field keeps default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_selfcheck_config'`

- [ ] **Step 3: Write minimal implementation**

In `src/core/config.py`, import `SelfCheckConfig` in the existing types import block, then add this loader (match the `load_interpret_config` idiom already in the file):

```python
def load_selfcheck_config(path: str) -> SelfCheckConfig:
    """Load self-check critic params/field limits from YAML; missing file -> defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return SelfCheckConfig()
    d = SelfCheckConfig()
    return SelfCheckConfig(
        model=data.get("model", d.model),
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        summary_max_chars=data.get("summary_max_chars", d.summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
        message_max_chars=data.get("message_max_chars", d.message_max_chars),
        max_flags_per_item=data.get("max_flags_per_item", d.max_flags_per_item),
        prompt_path=data.get("prompt_path", d.prompt_path),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/core/config.py tests/contract/test_selfcheck_config.py
git commit -m "feat(selfcheck): load_selfcheck_config loader

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Deterministic format-lint (pure function)

**Files:**
- Create: `src/pipeline/selfcheck.py`
- Test: `tests/contract/test_selfcheck_format_lint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_format_lint.py
from datetime import datetime, timezone
from src.core.types import Evidence, InterpretedItem, SelfCheckConfig, SourceType
from src.pipeline.selfcheck import format_lint

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _item(**over):
    base = dict(
        title_en="X", link="https://a/1", source="s", source_type=SourceType.MODEL,
        published_at=NOW, raw_summary="r", cluster_id="c", related_links=["https://a/2"],
        score=80.0, score_breakdown={"机构影响力": 80.0}, is_explore=False,
        title="标题", summary="摘要", takeaway="用法",
        tags=["#a", "#b", "#c"], evidence=[Evidence(claim="f", anchor="https://a/1")],
        interpretation_status="ok", eligible_for_must_read=True,
    )
    base.update(over)
    return InterpretedItem(**base)


def test_compliant_item_has_no_flags():
    assert format_lint(_item(), SelfCheckConfig()) == []


def test_wrong_tag_count_flagged():
    flags = format_lint(_item(tags=["#a", "#b"]), SelfCheckConfig())
    assert [f.code for f in flags] == ["format_lock"]
    assert flags[0].field == "tags"


def test_illegal_anchor_flagged():
    item = _item(evidence=[Evidence(claim="f", anchor="https://evil/x")])
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "evidence" for f in flags)


def test_eligible_but_no_evidence_flagged():
    item = _item(evidence=[], eligible_for_must_read=True)
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "evidence" for f in flags)


def test_oversize_summary_flagged():
    item = _item(summary="超" * 200)
    flags = format_lint(item, SelfCheckConfig())
    assert any(f.code == "format_lock" and f.field == "summary" for f in flags)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_format_lint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.selfcheck'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/pipeline/selfcheck.py
from __future__ import annotations

from src.core.types import InterpretedItem, QualityFlag, SelfCheckConfig


def format_lint(item: InterpretedItem, config: SelfCheckConfig) -> list[QualityFlag]:
    """Deterministic format-lock report (spec §5.2). Reports only, never modifies."""
    flags: list[QualityFlag] = []

    def warn(field: str, message: str) -> None:
        flags.append(
            QualityFlag(code="format_lock", severity="warn", field=field, message=message)
        )

    if len(item.title) > config.title_max_chars:
        warn("title", f"标题超长(>{config.title_max_chars})")
    if len(item.summary) > config.summary_max_chars:
        warn("summary", f"摘要超长(>{config.summary_max_chars})")
    if item.interpretation_status == "ok" and len(item.tags) != config.tags_count:
        warn("tags", f"标签数应为{config.tags_count},实为{len(item.tags)}")
    allowed = {item.link, *item.related_links}
    if any(e.anchor not in allowed for e in item.evidence):
        warn("evidence", "存在非法锚点(不在 link∪related_links)")
    if item.eligible_for_must_read:
        if len(item.evidence) < config.min_evidence:
            warn("evidence", f"必读条目证据不足(<{config.min_evidence})")
        if not item.takeaway:
            warn("takeaway", "必读条目缺 takeaway")
    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_format_lint.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/selfcheck.py tests/contract/test_selfcheck_format_lint.py
git commit -m "feat(selfcheck): deterministic format_lint pure function

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Critic prompt-build + parse (pure functions)

**Files:**
- Modify: `src/pipeline/selfcheck.py` (add `build_critic_prompt`, `parse_critic`)
- Test: `tests/contract/test_selfcheck_critic_parse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_critic_parse.py
import json
from datetime import datetime, timezone
from src.core.types import Evidence, InterpretedItem, SelfCheckConfig, SourceType
from src.pipeline.selfcheck import build_critic_prompt, parse_critic

NOW = datetime(2026, 6, 16, tzinfo=timezone.utc)


def _item():
    return InterpretedItem(
        title_en="X", link="https://a/1", source="s", source_type=SourceType.MODEL,
        published_at=NOW, raw_summary="原始摘要文本", cluster_id="c", related_links=[],
        score=80.0, score_breakdown={"机构影响力": 80.0}, is_explore=False,
        title="标题", summary="摘要", takeaway="用法", hot_take="锐评",
        tags=["#a", "#b", "#c"], evidence=[Evidence(claim="f", anchor="https://a/1")],
        interpretation_status="ok", eligible_for_must_read=True,
    )


def test_build_prompt_substitutes_fields():
    tpl = "T={{title}} S={{summary}} TA={{takeaway}} HT={{hot_take}} RAW={{raw_summary}} EV={{evidence}}"
    out = build_critic_prompt(_item(), tpl)
    assert "T=标题" in out and "TA=用法" in out and "RAW=原始摘要文本" in out


def test_parse_maps_codes_and_severity():
    raw = json.dumps({
        "consistency": [{"field": "takeaway", "message": "原文没说"}],
        "ai_slop": [{"field": "hot_take", "message": "套话"}],
    })
    flags = parse_critic(raw, SelfCheckConfig())
    by = {f.code: f for f in flags}
    assert by["consistency"].severity == "warn" and by["consistency"].field == "takeaway"
    assert by["ai_slop"].severity == "info"


def test_parse_truncates_message_and_caps_count():
    cfg = SelfCheckConfig(message_max_chars=5, max_flags_per_item=2)
    raw = json.dumps({"ai_slop": [{"field": "summary", "message": "一二三四五六七八"}] * 4})
    flags = parse_critic(raw, cfg)
    assert len(flags) == 2  # capped
    assert all(len(f.message) <= 5 for f in flags)


def test_parse_illegal_field_becomes_star():
    raw = json.dumps({"consistency": [{"field": "bogus", "message": "x"}]})
    flags = parse_critic(raw, SelfCheckConfig())
    assert flags[0].field == "*"


def test_parse_invalid_json_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_critic("not json", SelfCheckConfig())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_critic_parse.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_critic_prompt'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pipeline/selfcheck.py` (and add `import json` at top):

```python
import json

_FIELD_WHITELIST = {"takeaway", "summary", "hot_take", "tags", "evidence"}
_CODE_SEVERITY = {"consistency": "warn", "ai_slop": "info"}


def build_critic_prompt(item: InterpretedItem, template: str) -> str:
    """Render the critic prompt by substituting {{name}} placeholders (spec §5.3)."""
    ev = "\n".join(f"- {e.claim} @ {e.anchor}" for e in item.evidence)
    repl = {
        "{{title}}": item.title,
        "{{summary}}": item.summary,
        "{{takeaway}}": item.takeaway,
        "{{hot_take}}": item.hot_take,
        "{{title_en}}": item.title_en,
        "{{raw_summary}}": item.raw_summary or "",
        "{{evidence}}": ev,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def parse_critic(raw: str, config: SelfCheckConfig) -> list[QualityFlag]:
    """Parse critic JSON into flags (spec §5.3). Raises ValueError on bad JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON critic output: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("critic output is not a JSON object")
    flags: list[QualityFlag] = []
    for code, severity in _CODE_SEVERITY.items():
        entries = data.get(code) or []
        if not isinstance(entries, list):
            continue
        for e in entries[: config.max_flags_per_item]:
            if not isinstance(e, dict):
                continue
            msg = str(e.get("message", "")).strip()[: config.message_max_chars]
            if not msg:
                continue
            field = str(e.get("field", "")).strip()
            if field not in _FIELD_WHITELIST:
                field = "*"
            flags.append(QualityFlag(code=code, severity=severity, field=field, message=msg))
    return flags
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_critic_parse.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/selfcheck.py tests/contract/test_selfcheck_critic_parse.py
git commit -m "feat(selfcheck): critic prompt-build + parse pure functions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: self_check orchestrator + observability

**Files:**
- Modify: `src/pipeline/selfcheck.py` (add `check_item`, `self_check`)
- Test: `tests/golden/test_selfcheck.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/golden/test_selfcheck.py
import json
import logging
from datetime import datetime, timezone

from src.core.types import (
    Evidence, InterpretConfig, InterpretResult, InterpretedItem,
    RunContext, SelfCheckConfig, SourceType,
)
from src.pipeline.selfcheck import self_check
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 6, 16, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-selfcheck"))


def _item(link, eligible=True, status="ok", **over):
    base = dict(
        title_en="X", link=link, source="s", source_type=SourceType.MODEL,
        published_at=NOW, raw_summary="原始摘要", cluster_id="c", related_links=[],
        score=80.0, score_breakdown={"机构影响力": 80.0}, is_explore=False,
        title="标题", summary="摘要", takeaway="用法", hot_take="锐评",
        tags=["#a", "#b", "#c"], evidence=[Evidence(claim="f", anchor=link)],
        interpretation_status=status, eligible_for_must_read=eligible,
    )
    base.update(over)
    return InterpretedItem(**base)


def _result(items):
    ok = sum(1 for i in items if i.interpretation_status == "ok")
    return InterpretResult(
        interpreted_items=items, daily_take="看点", input_count=len(items),
        interpreted_count=ok, fallback_count=len(items) - ok, is_silent=False,
    )


CLEAN = json.dumps({"consistency": [], "ai_slop": []})


def test_happy_no_flags_item_unchanged():
    items = [_item("https://a/1")]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    out = res.interpreted_items[0]
    assert out.quality_flags == [] and res.flagged_count == 0
    assert res.checked_count == 1
    # advisor purity: every other field identical to input
    assert out.model_dump(exclude={"quality_flags"}) == items[0].model_dump(exclude={"quality_flags"})
    assert res.daily_take == "看点"  # passthrough


def test_consistency_and_ai_slop_flags():
    raw = json.dumps({
        "consistency": [{"field": "takeaway", "message": "原文没说"}],
        "ai_slop": [{"field": "hot_take", "message": "套话"}],
    })
    items = [_item("https://a/1")]
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), FakeLLMProvider({"https://a/1": raw}))
    codes = sorted(f.code for f in res.interpreted_items[0].quality_flags)
    assert codes == ["ai_slop", "consistency"]
    assert res.flag_count_by_code == {"consistency": 1, "ai_slop": 1}


def test_non_eligible_skips_critic():
    items = [_item("https://a/1", eligible=False)]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    assert res.checked_count == 0
    assert llm.calls == []  # critic never called on non-eligible
    assert all(f.code != "consistency" for f in res.interpreted_items[0].quality_flags)


def test_critic_failure_no_semantic_flag():
    items = [_item("https://a/1")]
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), FailingLLMProvider())
    flags = res.interpreted_items[0].quality_flags
    assert all(f.code == "format_lock" for f in flags)  # only deterministic survives
    assert res.llm_error_count == 1


def test_format_lint_runs_without_eligible_critic():
    # non-eligible but malformed tags -> format_lock flag, no critic call
    items = [_item("https://a/1", eligible=False, tags=["#a"])]
    llm = FakeLLMProvider({"https://a/1": CLEAN})
    res = self_check(_result(items), SelfCheckConfig(), _ctx(), llm)
    assert any(f.code == "format_lock" and f.field == "tags" for f in res.interpreted_items[0].quality_flags)
    assert llm.calls == []


def test_silent_input_skips_llm():
    empty = InterpretResult(
        interpreted_items=[], daily_take=None, input_count=0,
        interpreted_count=0, fallback_count=0, is_silent=True,
    )
    llm = FakeLLMProvider({})
    res = self_check(empty, SelfCheckConfig(), _ctx(), llm)
    assert res.is_silent and res.checked_count == 0 and llm.calls == []


def test_determinism():
    items = [_item("https://a/1")]
    llm1 = FakeLLMProvider({"https://a/1": CLEAN})
    llm2 = FakeLLMProvider({"https://a/1": CLEAN})
    r1 = self_check(_result(items), SelfCheckConfig(), _ctx(), llm1)
    r2 = self_check(_result(items), SelfCheckConfig(), _ctx(), llm2)
    assert [f.model_dump() for f in r1.interpreted_items[0].quality_flags] == \
           [f.model_dump() for f in r2.interpreted_items[0].quality_flags]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/golden/test_selfcheck.py -v`
Expected: FAIL with `ImportError: cannot import name 'self_check'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pipeline/selfcheck.py` (add the new imports at top):

```python
from src.core.prompts import load_prompt
from src.core.types import (
    InterpretResult,
    RunContext,
    SelfCheckResult,
)
from src.observability.events import emit


def check_item(item, template, config, llm, logger=None):
    """Per-item flags = format_lint + critic. Critic only matters for eligible items.
    Returns (flags, llm_errored: bool). Never raises (advisor)."""
    flags = format_lint(item, config)
    if not item.eligible_for_must_read:
        return flags, False
    try:
        prompt = build_critic_prompt(item, template)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        flags = flags + parse_critic(raw, config)
        return flags, False
    except Exception as e:
        if logger is not None:
            emit(logger, "selfcheck_error", link=item.link, error_type=type(e).__name__)
        return flags, True


def self_check(
    result: InterpretResult, config: SelfCheckConfig, ctx: RunContext, llm
) -> SelfCheckResult:
    """Advisor pass (spec §3, §5). Attaches quality_flags; never gates/drops/edits."""
    emit(ctx.logger, "selfcheck_start", run_id=ctx.run_id, input_count=result.input_count)
    if result.is_silent or not result.interpreted_items:
        emit(ctx.logger, "selfcheck_done", checked_count=0, flagged_count=0,
             flag_count_by_code={}, llm_error_count=0, silent=True)
        return SelfCheckResult(
            interpreted_items=result.interpreted_items, daily_take=result.daily_take,
            checked_count=0, flagged_count=0, flag_count_by_code={},
            llm_error_count=0, is_silent=result.is_silent,
        )

    template = load_prompt(config.prompt_path)
    out: list[InterpretedItem] = []
    checked = flagged = errors = 0
    by_code: dict[str, int] = {}
    for item in result.interpreted_items:
        flags, errored = check_item(item, template, config, llm, logger=ctx.logger)
        if item.eligible_for_must_read:
            checked += 1
        if errored:
            errors += 1
        if flags:
            flagged += 1
        for f in flags:
            by_code[f.code] = by_code.get(f.code, 0) + 1
        annotated = item.model_copy(update={"quality_flags": flags})
        emit(ctx.logger, "item_self_checked", link=item.link,
             flag_codes=[f.code for f in flags], n_flags=len(flags))
        out.append(annotated)

    emit(ctx.logger, "selfcheck_done", checked_count=checked, flagged_count=flagged,
         flag_count_by_code=by_code, llm_error_count=errors, silent=False)
    return SelfCheckResult(
        interpreted_items=out, daily_take=result.daily_take, checked_count=checked,
        flagged_count=flagged, flag_count_by_code=by_code, llm_error_count=errors,
        is_silent=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/golden/test_selfcheck.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/selfcheck.py tests/golden/test_selfcheck.py
git commit -m "feat(selfcheck): self_check orchestrator + observability (advisor)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Prompt + config files

**Files:**
- Create: `src/prompts/selfcheck.md`
- Create: `config/selfcheck.yaml`
- Test: `tests/contract/test_selfcheck_assets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_assets.py
from src.core.config import load_selfcheck_config
from src.core.prompts import load_prompt


def test_config_file_loads_and_points_to_existing_prompt():
    cfg = load_selfcheck_config("config/selfcheck.yaml")
    body = load_prompt(cfg.prompt_path)
    # prompt must expose the placeholders the builder substitutes
    for ph in ("{{takeaway}}", "{{hot_take}}", "{{raw_summary}}", "{{evidence}}"):
        assert ph in body
    # critic must be told to output the two-key JSON structure
    assert "consistency" in body and "ai_slop" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_assets.py -v`
Expected: FAIL — `config/selfcheck.yaml` missing → loader returns defaults pointing at `src/prompts/selfcheck.md`, which `load_prompt` cannot open (`FileNotFoundError`).

- [ ] **Step 3: Create the config file**

```yaml
# config/selfcheck.yaml
model: "Qwen/Qwen2.5-7B-Instruct"
temperature: 0.0
max_tokens: 600
timeout_s: 60
title_max_chars: 64
summary_max_chars: 120
tags_count: 3
min_evidence: 1
message_max_chars: 120
max_flags_per_item: 3
prompt_path: "src/prompts/selfcheck.md"
```

- [ ] **Step 4: Create the prompt file**

```markdown
<!-- src/prompts/selfcheck.md -->
你是 AI 日报的质量自检员。只做"挑刺"，不要改写、不要补内容。仅依据下面给出的文本判断，**不得引入任何外部知识或联网信息**。

待检条目：
- 中文标题：{{title}}
- 中文摘要：{{summary}}
- takeaway（怎么用）：{{takeaway}}
- 锐评 hot_take：{{hot_take}}
- 原始英文标题：{{title_en}}
- 原始摘要（手头唯一可信原文）：{{raw_summary}}
- 证据链（claim @ 锚点）：
{{evidence}}

请做两类检查：

1. consistency（内部一致性 / 防事实不实）：takeaway / summary / hot_take 里出现的关键事实，是否能从"原始摘要 + 原始标题 + 证据链"推出？凡是原文没有依据、像是编造或夸大的，逐条列出。能推出的不要列。

2. ai_slop（防 AI 味）：hot_take / summary 是否套话、空洞无判断、AI 腔、丢了"有态度有判断"的作者风格？逐条列出，能改进的点说清楚。没有就不列。

只输出 JSON，不要解释，结构固定：

{
  "consistency": [{"field": "takeaway", "message": "一句话说清问题"}],
  "ai_slop": [{"field": "hot_take", "message": "一句话说清问题"}]
}

field 取值限定：takeaway | summary | hot_take | tags | evidence。没有问题时对应数组留空 []。
```

> Note: the `<!-- ... -->` first line is a human label; `load_prompt` reads the file verbatim and the builder only substitutes `{{...}}`, so the comment is harmless. Keep it or drop it — the test only checks for the placeholders and the two code words.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_assets.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add src/prompts/selfcheck.md config/selfcheck.yaml tests/contract/test_selfcheck_assets.py
git commit -m "feat(selfcheck): critic prompt + config asset

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: CLI dry-run hook (`--selfcheck`)

**Files:**
- Modify: `src/cli.py` (add `run_dry_selfcheck`, `--selfcheck` flag, dispatch)
- Test: `tests/contract/test_selfcheck_cli.py`

> `run_dry_interpret` (`src/cli.py:137-174`) has signature `(registry_path, now=None, embedder=None, llm=None) -> dict`: it inlines collect→dedup→score→interpret and **returns a dict** (the caller in `main` prints it). Mirror that shape exactly — same params, same inline sequence, return a dict. Do NOT print inside the function.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_selfcheck_cli.py
import inspect
import src.cli as cli


def test_cli_exposes_selfcheck_entrypoint():
    assert hasattr(cli, "run_dry_selfcheck")
    # signature mirrors run_dry_interpret (same dependencies wired in)
    assert list(inspect.signature(cli.run_dry_selfcheck).parameters.keys()) == \
           list(inspect.signature(cli.run_dry_interpret).parameters.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_selfcheck_cli.py -v`
Expected: FAIL with `AttributeError: module 'src.cli' has no attribute 'run_dry_selfcheck'`

- [ ] **Step 3: Write minimal implementation**

In `src/cli.py`: add imports next to the existing `from src.core.config import (...)` block and the `from src.pipeline.interpret import interpret` line —

```python
from src.core.config import load_selfcheck_config  # add to the config import group
from src.pipeline.selfcheck import self_check
```

Add this function directly after `run_dry_interpret` (ends at `src/cli.py:174`). It copies `run_dry_interpret`'s exact body (collect → dedup → score → interpret), then runs `self_check`, and returns a dict:

```python
def run_dry_selfcheck(
    registry_path: str, now: datetime | None = None, embedder=None, llm=None
) -> dict:
    now = now or datetime.now(timezone.utc)
    logger = logging.getLogger("ai-newsday")
    ctx = RunContext(run_id=str(uuid.uuid4()), now=now, logger=logger)

    coll_cfg = CollectionConfig(sources_registry_path=registry_path)
    coll = asyncio.run(collect(coll_cfg, ctx))

    dcfg = load_dedup_config("config/dedup.yaml")
    dcfg.sources_registry_path = registry_path
    if embedder is None:
        embedder = ModelScopeEmbedder(
            api_key=os.environ.get("MODELSCOPE_API_KEY", ""),
            model=dcfg.embedding_model,
            batch_size=dcfg.batch_size,
        )
    dres = dedup(coll.items, dcfg, ctx, embedder=embedder, store=InMemoryVectorStore())

    scfg = load_scoring_config("config/scoring.yaml")
    scfg.sources_registry_path = registry_path
    sres = score(dres.deduped_items, scfg, ctx)

    icfg = load_interpret_config("config/interpret.yaml")
    if llm is None:
        llm = _make_llm(icfg)
    ires = interpret(sres.selected_items, icfg, ctx, llm)

    sccfg = load_selfcheck_config("config/selfcheck.yaml")
    sc = self_check(ires, sccfg, ctx, llm)
    return {
        "run_id": ctx.run_id,
        "now": now.isoformat(),
        "checked_count": sc.checked_count,
        "flagged_count": sc.flagged_count,
        "flag_count_by_code": sc.flag_count_by_code,
        "llm_error_count": sc.llm_error_count,
        "is_silent": sc.is_silent,
        "daily_take": sc.daily_take,
        "interpreted_items": [it.model_dump(mode="json") for it in sc.interpreted_items],
    }
```

In the argument parser, right after the `--interpret` flag (`src/cli.py:508`):

```python
    parser.add_argument(
        "--selfcheck",
        action="store_true",
        help="chain collect -> ... -> interpret -> self_check, print SelfCheckResult JSON",
    )
```

In `main`, add a dispatch branch **before** the `--interpret` branch at `src/cli.py:553` (so the longer chain wins if both flags are set), mirroring the surrounding branches exactly:

```python
    if args.dry_run and args.selfcheck:
        out = run_dry_selfcheck(registry_path=args.registry)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_selfcheck_cli.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `pytest -q`
Expected: PASS (all green, including pre-existing interpret/review/publish suites)

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/contract/test_selfcheck_cli.py
git commit -m "feat(selfcheck): --selfcheck dry-run hook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Write the contract spec

**Files:**
- Create: `docs/specs/selfcheck.md`

- [ ] **Step 1: Author the layer spec**

Promote the design doc (`docs/superpowers/specs/2026-06-16-quality-selfcheck-layer-design.md`) into a peer of the other layer specs at `docs/specs/selfcheck.md`. Keep sections §1–§13 from the design doc; update file-path references to the real implemented paths (`src/pipeline/selfcheck.py`, `config/selfcheck.yaml`, `src/prompts/selfcheck.md`, the test files created above). This is the SSOT contract per CLAUDE.md ("先改 spec").

- [ ] **Step 2: Commit**

```bash
git add docs/specs/selfcheck.md
git commit -m "docs(selfcheck): layer contract spec

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Why no pipeline-chain wiring beyond the dry-run hook?** `quality_flags` rides on `InterpretedItem`, which `ReviewedItem` extends — so flags flow to review/publish automatically with zero wiring. The review-UI surfacing of flags is explicitly out of scope (design §2). Don't add it.
- **Advisor discipline (design §8 invariant 1):** `self_check` must never mutate any field other than `quality_flags`. Use `model_copy(update=...)`, never in-place edits. Task 5's `test_happy_no_flags_item_unchanged` enforces this.
- **No-fabrication (design §8 invariant 3):** a critic exception must yield zero semantic flags — never a "looks fine" or "looks broken" flag. Only `format_lint` (deterministic) may flag on a critic failure.
- **Run the whole suite before the final commit** (`pytest -q`) — the new `quality_flags` field touches the shared `InterpretedItem` model that interpret/review/publish all serialize.
