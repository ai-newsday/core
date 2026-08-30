# Entity Fact-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the interpret LLM isn't sure which company/model a source is actually about, make it say so explicitly (`entity_confident: false`) instead of confidently inventing a name — surface that uncertainty as a review-card badge so a human decides whether to trust/fix it, without blocking or auto-editing the card.

**Architecture:** Add one boolean field to the existing `interpret_item.md` JSON output. `interpret.py::build_ok_item` reads it and — when false — appends a `QualityFlag(code="entity_uncertain", ...)` to `InterpretedItem.quality_flags`, a field that already exists on the type (`src/core/types.py:235`) but has never been populated on the production path (only the never-wired `selfcheck.py` writes it). The review card renderer (`tick.py::_build_card` → `telegram_polling.py::_make_card_message`) reads that flag and prefixes a `🔎 [待核实]` badge, reusing the exact pattern already used for the `⚠️ [未解读]` extractive-fallback badge. No new pipeline step, no new config, no new UI — a human who sees the badge corrects the entity via the existing `edit` decision action (`src/pipeline/review.py::apply_decision`, already supports overriding `title`/`body`).

**Tech Stack:** Python 3.12, pydantic (`src/core/types.py::QualityFlag`), pytest.

## Global Constraints

- Zero new LLM calls — the field rides along in the existing single `interpret_item()` call.
- Zero new UI/interaction surface — badge is display-only; correction uses the existing `edit` action.
- `extractive_fallback` path is unaffected — no LLM-generated `entity_confident` exists there, so `quality_flags` stays empty on that path (unchanged behavior).
- Missing `entity_confident` in LLM output defaults to confident (`True`, no flag) — backward compatible.
- Badge must coexist with the existing `⚠️ [未解读]` badge — a card can show both, one, or neither.

---

## File Structure

- Modify `src/prompts/interpret_item.md` — add `entity_confident: bool` field + rule text.
- Modify `src/pipeline/interpret.py` — `build_ok_item` appends a `QualityFlag` when `entity_confident` is false.
- Modify `src/pipeline/tick.py` — `_build_card` exposes whether the item carries an `entity_uncertain` flag.
- Modify `src/notifiers/telegram_polling.py` — `_make_card_message` prefixes the `🔎 [待核实]` badge.
- Modify `tests/contract/test_prompts.py` — prompt content assertion.
- Modify `tests/contract/test_interpret_unit.py` — `build_ok_item` flag-append behavior.
- Modify `tests/contract/test_telegram_notifier.py` — badge rendering + coexistence with the fallback badge.

---

### Task 1: Prompt rule — `entity_confident` field

**Files:**
- Modify: `src/prompts/interpret_item.md`
- Test: `tests/contract/test_prompts.py`

**Interfaces:**
- Produces: `interpret_item.md` JSON schema gains `"entity_confident": true` as a top-level output key, consumed by Task 2's `build_ok_item`.

- [ ] **Step 1: Write the failing test**

Append to `tests/contract/test_prompts.py`:

```python
def test_interpret_prompt_has_entity_confident_field():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "entity_confident" in t
    assert '"entity_confident"' in t
    assert "宁可标 false" in t
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_prompts.py -v -k entity_confident`
Expected: FAIL — assertion errors (strings not found)

- [ ] **Step 3: Edit the prompt**

This plan assumes Task 2 of `docs/superpowers/plans/2026-08-28-content-certainty-and-title-hooks.md` has already landed (adds `content_certain` + title guards to the same file). If executed before that plan, apply this edit to the prompt as it exists in the repo right now instead of assuming the other plan's text is present — the diff below is additive and independent either way.

In `src/prompts/interpret_item.md`, add a new bullet directly after the `content_certain` rule (or after the `relevant` rule if Task 2 of the other plan hasn't landed yet), and update the JSON schema line:

```markdown
- `entity_confident`：布尔值。当 `title`/`body` 里提到的公司名/模型名**能在原文摘要或链接里找到明确依据**时为 true；当你是靠"来源"字段的名字联想/推测得出的，或原文信息不足以确定具体是哪家公司/哪个型号时，为 false——**宁可标 false，也不要标 true 然后编一个名字**。
```

And change the JSON schema output line from:

```
{"title": "...", "body": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}], "relevant": true, "content_certain": true}
```

to:

```
{"title": "...", "body": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}], "relevant": true, "content_certain": true, "entity_confident": true}
```

(If the content-certainty plan has not landed yet, the baseline schema line will instead read `..."relevant": true}` — append `, "entity_confident": true}` to whatever the current line ends with, keeping valid JSON.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/contract/test_prompts.py -v`
Expected: PASS (all — including every pre-existing prompt test)

- [ ] **Step 5: Commit**

```bash
git add src/prompts/interpret_item.md tests/contract/test_prompts.py
git commit -m "feat(interpret): add entity_confident field to interpret_item.md prompt"
```

---

### Task 2: `build_ok_item` appends `QualityFlag` when `entity_confident` is false

**Files:**
- Modify: `src/pipeline/interpret.py` (`build_ok_item`)
- Test: `tests/contract/test_interpret_unit.py`

**Interfaces:**
- Consumes: `src/core/types.py::QualityFlag(code: str, severity: str, field: str, message: str)` (existing type, no changes).
- Produces: `InterpretedItem.quality_flags` now non-empty on the `ok` path whenever `entity_confident` is false in the parsed LLM JSON. Flag shape: `QualityFlag(code="entity_uncertain", severity="warn", field="title", message="模型/公司归属未完全确定, 请核实后再发布")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_interpret_unit.py`:

```python
def test_build_ok_item_entity_confident_false_appends_quality_flag():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
        "entity_confident": False,
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert len(out.quality_flags) == 1
    flag = out.quality_flags[0]
    assert flag.code == "entity_uncertain"
    assert flag.severity == "warn"
    assert flag.field == "title"
    assert flag.message


def test_build_ok_item_entity_confident_true_no_flag():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
        "entity_confident": True,
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.quality_flags == []


def test_build_ok_item_entity_confident_missing_defaults_no_flag():
    it = _scored()
    parsed = {
        "title": "t",
        "body": "s。",
        "tags": ["#a", "#b", "#c"],
        "evidence": [{"claim": "c", "anchor": "https://hf.co/glm5"}],
    }
    out = build_ok_item(parsed, it, InterpretConfig())
    assert out.quality_flags == []


def test_extractive_fallback_has_no_quality_flags():
    it = _scored()
    out = extractive_fallback(it, InterpretConfig())
    assert out.quality_flags == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v -k entity_confident`
Expected: FAIL — `assert len(out.quality_flags) == 1` fails with `0 == 1` (field not yet consumed)

- [ ] **Step 3: Implement in `src/pipeline/interpret.py`**

Add the `QualityFlag` import at the top of the file:

```python
from src.core.types import (
    Evidence,
    InterpretConfig,
    InterpretedItem,
    InterpretResult,
    QualityFlag,
    RunContext,
    ScoredItem,
)
```

In `build_ok_item`, add the flag-building logic right before constructing the return value. If Task 3 of the content-certainty plan has already landed, `build_ok_item` looks like the version below (with `score`/`breakdown` already present); otherwise apply the same `quality_flags` addition to the current unmodified function body:

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
    quality_flags: list[QualityFlag] = []
    if not parsed.get("entity_confident", True):
        quality_flags.append(
            QualityFlag(
                code="entity_uncertain",
                severity="warn",
                field="title",
                message="模型/公司归属未完全确定, 请核实后再发布",
            )
        )
    return InterpretedItem(
        **{**item.model_dump(), "score": score, "score_breakdown": breakdown},
        title=title,
        body=body,
        tags=[str(t) for t in tags],
        evidence=evidence,
        interpretation_status="ok",
        eligible_for_must_read=eligible,
        relevant=relevant,
        quality_flags=quality_flags,
    )
```

(If the content-certainty plan has not landed, drop the `uncertain_content_penalty` parameter and the `score`/`breakdown`/`content_certain` lines, keep everything else — including the `**item.model_dump()` unpack via the plain `**item.model_dump()` form already in the file — and just add the `quality_flags` block plus `quality_flags=quality_flags` on the return.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_interpret_unit.py -v`
Expected: PASS (all — including every pre-existing test in the file)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py
git commit -m "feat(interpret): flag entity_uncertain items via quality_flags"
```

---

### Task 3: Review-card badge — `_build_card` + `_make_card_message`

**Files:**
- Modify: `src/pipeline/tick.py:49-61` (`_build_card`)
- Modify: `src/notifiers/telegram_polling.py:23-54` (`_make_card_message`)
- Test: `tests/contract/test_telegram_notifier.py`

**Interfaces:**
- Consumes: `InterpretedItem.quality_flags: list[QualityFlag]` (Task 2).
- Produces: `_build_card(item)` returns a dict with a new key `"entity_uncertain": bool`; `_make_card_message(item_id, card)` prefixes `"🔎 [待核实] "` to the message when that key is truthy, coexisting with the existing `"⚠️ [未解读] "` prefix.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_telegram_notifier.py`:

```python
def test_card_entity_uncertain_shows_badge():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "某公司发布新模型",
        "title_en": "Some Company releases new model",
        "source_label": "官方",
        "source": "x-ai-company",
        "link": "https://x/1",
        "score": 90,
        "signals": {},
        "body": "正文",
        "tags": [],
        "status": "ok",
        "entity_uncertain": True,
    }
    msg = _make_card_message("id1", card)
    assert msg.startswith("🔎 [待核实] ")


def test_card_entity_confident_no_badge():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "中文标题",
        "title_en": "Title",
        "source_label": "论文",
        "source": "hf-papers",
        "link": "https://x/1",
        "score": 88,
        "signals": {},
        "body": "正文",
        "tags": [],
        "status": "ok",
        "entity_uncertain": False,
    }
    msg = _make_card_message("id1", card)
    assert "待核实" not in msg


def test_card_entity_uncertain_coexists_with_fallback_badge():
    from src.notifiers.telegram_polling import _make_card_message

    card = {
        "title_zh": "标题",
        "title_en": "Title",
        "source_label": "博客 / 工具",
        "source": "gh-trending-ai",
        "link": "https://x/1",
        "score": 95,
        "signals": {},
        "body": "正文",
        "tags": [],
        "status": "extractive_fallback",
        "entity_uncertain": True,
    }
    msg = _make_card_message("id1", card)
    assert "⚠️ [未解读]" in msg
    assert "🔎 [待核实]" in msg


def test_build_card_includes_entity_uncertain_flag():
    import hashlib
    from datetime import datetime, timezone

    from src.core.types import Evidence, Genre, InterpretedItem, Publisher, QualityFlag
    from src.pipeline.tick import _build_card

    item = InterpretedItem(
        title_en="x",
        link="https://x/1",
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        signals={},
        cluster_id=hashlib.sha256(b"x").hexdigest()[:16],
        related_links=[],
        score=80,
        score_breakdown={"技术价值": 80.0},
        title="x",
        body="b",
        tags=["#a"],
        evidence=[Evidence(claim="c", anchor="https://x/1")],
        interpretation_status="ok",
        eligible_for_must_read=True,
        quality_flags=[
            QualityFlag(
                code="entity_uncertain", severity="warn", field="title", message="待核实"
            )
        ],
    )
    card = _build_card(item)
    assert card["entity_uncertain"] is True


def test_build_card_no_entity_uncertain_flag_when_confident():
    import hashlib
    from datetime import datetime, timezone

    from src.core.types import Evidence, Genre, InterpretedItem, Publisher
    from src.pipeline.tick import _build_card

    item = InterpretedItem(
        title_en="x",
        link="https://x/1",
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=datetime(2026, 6, 26, tzinfo=timezone.utc),
        signals={},
        cluster_id=hashlib.sha256(b"x").hexdigest()[:16],
        related_links=[],
        score=80,
        score_breakdown={"技术价值": 80.0},
        title="x",
        body="b",
        tags=["#a"],
        evidence=[Evidence(claim="c", anchor="https://x/1")],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )
    card = _build_card(item)
    assert card["entity_uncertain"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_telegram_notifier.py -v -k entity_uncertain`
Expected: FAIL — `KeyError: 'entity_uncertain'` (from `_build_card` tests) and badge assertions failing (message doesn't start with the new prefix)

- [ ] **Step 3: Implement `_build_card` in `src/pipeline/tick.py`**

Change:

```python
def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
        "status": item.interpretation_status,
    }
```

to:

```python
def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
        "status": item.interpretation_status,
        "entity_uncertain": any(f.code == "entity_uncertain" for f in item.quality_flags),
    }
```

- [ ] **Step 4: Implement `_make_card_message` in `src/notifiers/telegram_polling.py`**

Change the badge section from:

```python
    # 病2: interpret 回退卡 → 加视觉徽章, 用户立刻知道这是降级输出(非翻译模块故障)
    if card.get("status") == "extractive_fallback":
        cover = "⚠️ [未解读] " + cover
    return cover + "\n\n" + body + (f"\n\n{tags}" if tags else "")
```

to:

```python
    # 病2: interpret 回退卡 → 加视觉徽章, 用户立刻知道这是降级输出(非翻译模块故障)
    if card.get("status") == "extractive_fallback":
        cover = "⚠️ [未解读] " + cover
    # 实体归属不确定 → 独立徽章, 提示人工核实后再采信(可与"未解读"共存, 互不影响)
    if card.get("entity_uncertain"):
        cover = "🔎 [待核实] " + cover
    return cover + "\n\n" + body + (f"\n\n{tags}" if tags else "")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_telegram_notifier.py -v`
Expected: PASS (all — including every pre-existing test in the file)

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run pytest tests/ -x -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/pipeline/tick.py src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py
git commit -m "feat(review): show 待核实 badge on review cards with uncertain entity attribution"
```

---

## Self-Review

**Spec coverage:**
- ✅ §1 `interpret_item.md` 新增 `entity_confident`: Task 1.
- ✅ §2 `build_ok_item` 消费该字段, 写入 `quality_flags`: Task 2.
- ✅ `extractive_fallback` 不需要该字段: explicit test in Task 2 (`test_extractive_fallback_has_no_quality_flags`).
- ✅ §3 审阅卡片可见徽章, 两者可共存: Task 3 (`test_card_entity_uncertain_coexists_with_fallback_badge`).
- ✅ §4 复用现有 `edit` 决策动作, 不新造机制: confirmed via codebase read (`src/pipeline/review.py::apply_decision` already supports `title`/`body` edits) — no code changes needed, no task required.
- ✅ 不做自动二次校验/搜索验证: confirmed — no such logic added anywhere in this plan.
- ✅ 不改变 `relevant=false` 既有语义: confirmed — `entity_confident` and `relevant` are read independently in `build_ok_item`, no interaction.

**Placeholder scan:** none — every step has literal code/text.

**Type consistency:** `QualityFlag(code="entity_uncertain", severity="warn", field="title", message=...)` matches the existing `QualityFlag` type signature (`src/core/types.py:220-224`) exactly. `_build_card`'s `"entity_uncertain"` key name matches what `_make_card_message` reads.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-28-entity-factcheck.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
