# Paper + Releases 降噪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `github_releases` items from showing up as raw untranslated/mid-word-truncated English, kill GitHub canary/rc noise, add a noise floor to `hf-papers`, and cap total GitHub-sourced content so it can't crowd out real company announcements.

**Architecture:** Four independent, config-driven changes layered onto the existing pure-function pipeline (collect → score → interpret → publish). No new modules, no new abstractions — each change extends an existing function/config that already does the analogous thing elsewhere in the codebase (`hn.py`'s `min_score` pattern, `apply_quota`'s group-and-truncate pattern, `InterpretConfig`'s existing `*_max_chars` fields).

**Tech Stack:** Python 3.12, pydantic (`RawItem`/`ScoredItem`/etc.), dataclasses (`InterpretConfig`/`PublishConfig`), pytest + pytest-asyncio, respx (HTTP mocking).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-14-paper-release-noise-reduction-design.md` — this plan implements §1–§5 exactly as designed there.
- No hardcoded thresholds — every new numeric value (`raw_summary_max_chars`, `min_score`, `adapter_quota`) lives in `config/*.yaml`, loaded via the existing `load_*_config` pattern in `src/core/config.py`.
- Pure functions stay pure — `apply_adapter_quota` takes/returns plain data, no I/O, matching `apply_quota` right next to it in `src/pipeline/score.py`.
- All 4 commits land in **one PR** on branch `feat/paper-release-noise-reduction` (already checked out in this worktree, based on `origin/master`), per user's explicit "一次性做完" — but each commit must leave `pytest` fully green (no partial/broken intermediate state).
- Run `pytest` (not a subset) before every commit to confirm no regressions. Also run `ruff check .` and `ruff format --check .` before each commit — CI gates on both and pytest doesn't catch lint issues.
- Do not touch `docs/specs/interpret.md`'s fallback contract (§5.3) — this plan only reduces how often fallback triggers and cleans up its output, it doesn't change the fallback mechanism itself.

---

## Task 1: Fix `_trim_to_sentence` cutting mid-version-number

**Files:**
- Modify: `src/pipeline/interpret.py:62-70` (`_SENT_ENDS` constant + `_trim_to_sentence` function)
- Test: `tests/contract/test_interpret_unit.py` (add tests near existing `test_trim_to_sentence_cuts_at_punctuation` at line 236)

**Interfaces:**
- Consumes: nothing new — pure refactor of an existing pure function.
- Produces: `_trim_to_sentence(text: str, n: int) -> str` — same signature, same behavior for all existing non-`.` cases; `.` now only counts as a sentence end when followed by whitespace or end-of-window. Tasks 2–4 don't call this function directly but rely on it staying correct.

- [ ] **Step 1: Write the failing test**

Add to `tests/contract/test_interpret_unit.py` right after the existing `test_trim_to_sentence_cuts_at_punctuation` (around line 241):

```python
def test_trim_to_sentence_does_not_cut_mid_version_number():
    from src.pipeline.interpret import _trim_to_sentence

    text = "Based on changes since v2.2.11-canary.3, this release adds streaming support for chat."
    # window of 40 chars lands right after "v2.2.11-canary." with the old bug
    out = _trim_to_sentence(text, 40)
    assert not out.endswith("canary.")
    assert out == text[:39] + "…"  # no real sentence end in window -> hard cut + ellipsis


def test_trim_to_sentence_dot_followed_by_space_still_counts():
    from src.pipeline.interpret import _trim_to_sentence

    text = "First sentence. Second sentence is much longer than the limit here."
    out = _trim_to_sentence(text, 20)
    assert out == "First sentence."  # "." followed by space still a valid cut point
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/contract/test_interpret_unit.py -v -k trim_to_sentence`
Expected: the two new tests FAIL (old code cuts at `canary.` because bare `.` counts as sentence end), the pre-existing `test_trim_to_sentence_cuts_at_punctuation` still PASSes.

- [ ] **Step 3: Fix `_trim_to_sentence`**

Replace lines 62-70 of `src/pipeline/interpret.py`:

```python
_SENT_ENDS = "。！？!?；;"


def _trim_to_sentence(text: str, n: int) -> str:
    """超长则截到上限内最后一个句末标点(含); 无标点则硬切 + 省略号。
    "." 只在其后紧跟空白或就是窗口末尾时才算句末, 避开版本号(v2.2.11-canary.3)/缩写(e.g.)。"""
    if len(text) <= n:
        return text
    window = text[:n]
    dot_cut = -1
    for i, ch in enumerate(window):
        if ch == "." and (i + 1 == len(window) or window[i + 1].isspace()):
            dot_cut = i
    cut = max([window.rfind(ch) for ch in _SENT_ENDS] + [dot_cut], default=-1)
    if cut >= 0:
        return window[: cut + 1]
    return text[: n - 1] + "…"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/contract/test_interpret_unit.py -v -k trim_to_sentence`
Expected: all 4 tests (2 new + 2 existing) PASS.

- [ ] **Step 5: Run full test suite + lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: all green (this function has no other callers changed yet).

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py
git commit -m "fix(interpret): _trim_to_sentence no longer cuts mid version-number/abbreviation

Bare '.' counted as a sentence end, cutting fallback output mid-string
like 'v2.2.11-canary.' (observed in content/posts/2026-07-11.md). Now
'.' only counts when followed by whitespace or end-of-window."
```

---

## Task 2: Cap `raw_summary` before it reaches the LLM prompt

**Files:**
- Modify: `src/core/types.py` (add `raw_summary_max_chars` field to `InterpretConfig`, around line 242 where `body_max_chars: int = 240` lives)
- Modify: `src/core/config.py:88-102` (`load_interpret_config` — add the new field to the constructed `InterpretConfig(...)`)
- Modify: `config/interpret.yaml` (add the new key)
- Modify: `src/pipeline/interpret.py:17-30` (`build_item_prompt` — accept `config`, truncate `raw_summary` via `_trim_to_sentence`)
- Modify: `src/pipeline/interpret.py:128` (`interpret_item` — pass `config` to `build_item_prompt`)
- Test: `tests/contract/test_interpret_unit.py` (update 2 existing `build_item_prompt` calls, add 1 new test)
- Test: `tests/contract/test_interpret_config.py` (add loader test)

**Interfaces:**
- Consumes: `_trim_to_sentence(text, n)` from Task 1 (already fixed).
- Produces: `InterpretConfig.raw_summary_max_chars: int` (default `1500`); `build_item_prompt(item: ScoredItem, template: str, config: InterpretConfig) -> str` — **signature changed**, now requires `config` as 3rd positional arg. Task 3/4 don't call this function, so no other call sites to update besides the ones listed here.

- [ ] **Step 1: Write the failing tests**

In `tests/contract/test_interpret_unit.py`, update the two existing calls (lines ~36-50) to pass a config, and add a new truncation test. Replace:

```python
def test_build_item_prompt_substitutes_double_brace_placeholders():
    tpl = "T={{title_en}} L={{link}} R={{related_links}} S={{raw_summary}} ST={{genre}}"
    out = build_item_prompt(_scored(), tpl)
    assert "T=GLM-5 released" in out
    assert "L=https://hf.co/glm5" in out
    assert "https://blog/glm5" in out
    assert "S=MoE open weights model." in out
    assert "ST=model" in out


def test_build_item_prompt_handles_empty_summary_and_links():
    out = build_item_prompt(
        _scored(raw_summary=None, related_links=[]), "S={{raw_summary}}|R={{related_links}}"
    )
    assert "S=|" in out
```

with:

```python
def test_build_item_prompt_substitutes_double_brace_placeholders():
    tpl = "T={{title_en}} L={{link}} R={{related_links}} S={{raw_summary}} ST={{genre}}"
    out = build_item_prompt(_scored(), tpl, InterpretConfig())
    assert "T=GLM-5 released" in out
    assert "L=https://hf.co/glm5" in out
    assert "https://blog/glm5" in out
    assert "S=MoE open weights model." in out
    assert "ST=model" in out


def test_build_item_prompt_handles_empty_summary_and_links():
    out = build_item_prompt(
        _scored(raw_summary=None, related_links=[]),
        "S={{raw_summary}}|R={{related_links}}",
        InterpretConfig(),
    )
    assert "S=|" in out


def test_build_item_prompt_truncates_oversized_raw_summary():
    long_summary = "A" * 5000
    cfg = InterpretConfig(raw_summary_max_chars=100)
    out = build_item_prompt(_scored(raw_summary=long_summary), "S={{raw_summary}}", cfg)
    # "AAAA...A" has no sentence-end punctuation -> hard cut + ellipsis at n=100
    assert out == "S=" + ("A" * 99) + "…"
    assert len(out) < len(long_summary)
```

`InterpretConfig` is already imported at the top of this test file (line 6), no new import needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/contract/test_interpret_unit.py -v -k build_item_prompt`
Expected: all 3 FAIL — the two existing ones with `TypeError: build_item_prompt() takes 2 positional arguments but 3 were given` (function doesn't accept `config` yet), the new one likewise.

- [ ] **Step 3: Add `raw_summary_max_chars` to `InterpretConfig`**

In `src/core/types.py`, find the `InterpretConfig` class (around line 231-246) and add the field right after `body_max_chars: int = 240`:

```python
    title_max_chars: int = 64
    body_max_chars: int = 240
    raw_summary_max_chars: int = 1500  # 防任意 adapter 的超长 raw_summary 撑爆 prompt
    tags_count: int = 3
```

- [ ] **Step 4: Wire it into the config loader**

In `src/core/config.py`, inside `load_interpret_config` (around line 88-102), add one line to the returned `InterpretConfig(...)` call, right after `body_max_chars`:

```python
    return InterpretConfig(
        model=data.get("model", d.model),
        models=data.get("models", d.models),
        fallback_models=data.get("fallback_models", d.fallback_models),
        providers=providers,
        temperature=data.get("temperature", d.temperature),
        max_tokens=data.get("max_tokens", d.max_tokens),
        timeout_s=data.get("timeout_s", d.timeout_s),
        title_max_chars=data.get("title_max_chars", d.title_max_chars),
        body_max_chars=data.get("body_max_chars", d.body_max_chars),
        raw_summary_max_chars=data.get("raw_summary_max_chars", d.raw_summary_max_chars),
        tags_count=data.get("tags_count", d.tags_count),
        min_evidence=data.get("min_evidence", d.min_evidence),
        item_prompt_path=data.get("item_prompt_path", d.item_prompt_path),
        daily_prompt_path=data.get("daily_prompt_path", d.daily_prompt_path),
    )
```

- [ ] **Step 5: Update `build_item_prompt` and its caller**

In `src/pipeline/interpret.py`, replace lines 17-30:

```python
def build_item_prompt(item: ScoredItem, template: str, config: InterpretConfig) -> str:
    """Render the per-item prompt by substituting {{name}} placeholders.
    Double-brace placeholders avoid clashing with JSON braces in the template.
    raw_summary is capped at config.raw_summary_max_chars so an oversized
    changelog/release body can't blow the LLM's prompt budget and force a
    fallback (spec §1)."""
    related = "\n".join(item.related_links)
    raw_summary = _trim_to_sentence(item.raw_summary or "", config.raw_summary_max_chars)
    repl = {
        "{{title_en}}": item.title_en,
        "{{source}}": item.source,
        "{{genre}}": item.genre.value,
        "{{link}}": item.link,
        "{{related_links}}": related,
        "{{raw_summary}}": raw_summary,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out
```

`_trim_to_sentence` is defined later in the same file (line 62 originally); Python resolves this fine since it's only called at runtime inside the function body, not at module-load time — no reordering needed.

Then update the one call site inside `interpret_item` (originally line 128):

```python
        prompt = build_item_prompt(item, item_template, config)
```

- [ ] **Step 6: Add the loader test**

In `tests/contract/test_interpret_config.py`, add:

```python
def test_interpret_config_has_raw_summary_max_chars_default():
    c = load_interpret_config("does/not/exist.yaml")
    assert c.raw_summary_max_chars == 1500


def test_interpret_config_loads_raw_summary_max_chars_override(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text("raw_summary_max_chars: 800\n", encoding="utf-8")
    c = load_interpret_config(str(p))
    assert c.raw_summary_max_chars == 800
```

- [ ] **Step 7: Add the config value to `config/interpret.yaml`**

Append after the `body_max_chars: 240` line in `config/interpret.yaml`:

```yaml
raw_summary_max_chars: 1500          # 防任意 adapter 的超长 raw_summary(如 GitHub release body)撑爆 prompt
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/contract/test_interpret_unit.py tests/contract/test_interpret_config.py -v`
Expected: all PASS, including the pre-existing tests in both files (no regressions).

- [ ] **Step 9: Run full suite + lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: all green. (Check specifically for any other `build_item_prompt(` call sites the grep in Step 1 might have missed — there should be none besides the 2 test calls and 1 production call already updated.)

- [ ] **Step 10: Commit**

```bash
git add src/core/types.py src/core/config.py src/pipeline/interpret.py config/interpret.yaml tests/contract/test_interpret_unit.py tests/contract/test_interpret_config.py
git commit -m "feat(interpret): cap raw_summary before it reaches the LLM prompt

InterpretConfig.raw_summary_max_chars (default 1500, config-driven) is
applied in build_item_prompt via the sentence-aware _trim_to_sentence.
Fixes github_releases items almost always overflowing the prompt and
falling back to raw untranslated text — applies to any adapter, not
just releases."
```

---

## Task 3: Filter GitHub prerelease builds + add hf-papers noise floor

**Files:**
- Modify: `src/adapters/sources/github_releases.py` (skip `prerelease` releases)
- Modify: `src/adapters/sources/hf_papers.py` (add `min_score` gate)
- Modify: `config/sources.yaml` (set `min_score: 15` on the `hf-papers` source entry, line 4)
- Test: `tests/contract/test_github_releases_adapter.py` (add prerelease test)
- Test: `tests/contract/test_hf_papers_adapter.py` (add min_score test)

**Interfaces:**
- Consumes: `SourceSpec.min_score: int | None` (already exists, used by `hn.py`).
- Produces: no new public interfaces — both adapters keep their existing `fetch(source, ctx, timeout_s) -> list[RawItem]` signature. Task 4 doesn't depend on these changes.

- [ ] **Step 1: Write the failing test for prerelease filtering**

In `tests/contract/test_github_releases_adapter.py`, add after `_release(...)` helper (around line 40):

```python
@respx.mock
async def test_releases_skips_prerelease():
    releases = [
        {**_release(tag="v0.3.40"), "prerelease": False},
        {
            **_release(
                tag="v0.3.41-canary.1",
                url="https://github.com/comfyanonymous/ComfyUI/releases/tag/v0.3.41-canary.1",
            ),
            "prerelease": True,
        },
    ]
    respx.get(_RELEASES).mock(return_value=httpx.Response(200, json=releases))
    respx.get(_REPO).mock(return_value=httpx.Response(200, json={"stargazers_count": 65000}))
    items = await GithubReleasesAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    assert items[0].title_en == "comfyui v0.3.40"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_github_releases_adapter.py -v -k prerelease`
Expected: FAIL — `assert len(items) == 1` fails because both releases (2) are currently returned; the adapter doesn't look at `prerelease` at all yet.

- [ ] **Step 3: Filter `prerelease` in `github_releases.py`**

In `src/adapters/sources/github_releases.py`, inside the `for r in releases:` loop, add the skip right after the loop starts (before the existing `published`/`tag`/`html_url` checks):

```python
        for r in releases:
            if r.get("prerelease"):
                continue
            published = r.get("published_at")
            tag = r.get("tag_name")
            html_url = r.get("html_url")
            if not published or not tag or not html_url:
                continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contract/test_github_releases_adapter.py -v`
Expected: all tests in this file PASS, including the new one and the 4 pre-existing ones (no regressions — none of them set `prerelease`, so `r.get("prerelease")` is `None`/falsy and they're unaffected).

- [ ] **Step 5: Write the failing test for hf-papers min_score**

In `tests/contract/test_hf_papers_adapter.py`, add:

```python
@respx.mock
async def test_hf_papers_filters_below_min_score():
    data = json.load(open("fixtures/sources/hf_papers_sample.json"))
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=data))
    spec = SourceSpec(
        name="hf-papers",
        url="https://huggingface.co/api/papers",
        genre=Genre.paper,
        publisher=Publisher.company,
        adapter="hf_papers",
        min_score=15,
    )
    items = await HFPapersAdapter().fetch(spec, _ctx(), timeout_s=15)
    # fixture has upvotes=42 and upvotes=7 -> only the 42 survives min_score=15
    assert len(items) == 1
    assert items[0].signals["upvotes"] == 42


@respx.mock
async def test_hf_papers_min_score_none_does_not_filter():
    data = json.load(open("fixtures/sources/hf_papers_sample.json"))
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=data))
    items = await HFPapersAdapter().fetch(_spec(), _ctx(), timeout_s=15)  # _spec() has min_score=None
    assert len(items) == 2  # unchanged — backward compatible
```

- [ ] **Step 6: Run tests to verify the new one fails**

Run: `pytest tests/contract/test_hf_papers_adapter.py -v -k min_score`
Expected: `test_hf_papers_filters_below_min_score` FAILs (`assert len(items) == 1` — currently returns 2, no filtering happens); `test_hf_papers_min_score_none_does_not_filter` PASSes already (documents current behavior, guards the no-op path).

- [ ] **Step 7: Add `min_score` gate to `hf_papers.py`**

In `src/adapters/sources/hf_papers.py`, inside the `for row in data:` loop, add the check right after `pid, title = ...` and before the `published` computation:

```python
        for row in data:
            paper = row.get("paper", {})
            pid, title = paper.get("id"), paper.get("title")
            upvotes = paper.get("upvotes")
            if source.min_score is not None and (upvotes or 0) < source.min_score:
                continue
            # 当日精选论文按"精选日"算新鲜度(submittedOnDailyAt), 否则 arxiv 原始
            # publishedAt 常早于采集时间窗, 整批精选集会被砍光。回退保旧行为。
            published = _parse_dt(
                paper.get("submittedOnDailyAt")
                or paper.get("publishedAt")
                or row.get("publishedAt")
            )
```

Note: `upvotes` is read once here and reused below where `signals["upvotes"]` is built — no duplicate `paper.get("upvotes")` call needed; the existing `signals = {"upvotes": paper.get("upvotes"), ...}` line further down can stay as-is (it's harmless to call `.get` twice, but if you want to dedupe, replace that line's `paper.get("upvotes")` with the local `upvotes` variable — optional cleanup, not required for correctness).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/contract/test_hf_papers_adapter.py -v`
Expected: all 4 tests in this file PASS (2 new + 2 pre-existing, no regressions).

- [ ] **Step 9: Set the threshold in `config/sources.yaml`**

In `config/sources.yaml`, line 4, change:

```yaml
- {name: hf-papers, url: "https://huggingface.co/api/daily_papers", genre: paper, publisher: company, adapter: hf_papers, status: working, priority: 1}
```

to:

```yaml
- {name: hf-papers, url: "https://huggingface.co/api/daily_papers", genre: paper, publisher: company, adapter: hf_papers, status: working, priority: 1, min_score: 15}
# min_score: 15 依据 docs/recent-papers.md 5 天样本尾部分布定(单日尾部低到 ~20, 头部 60-185),
# 过滤"几乎无人关注"的论文, 不误伤头部。见 docs/superpowers/specs/2026-07-14-paper-release-noise-reduction-design.md §4。
```

- [ ] **Step 10: Run full suite + lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: all green.

- [ ] **Step 11: dry-run smoke to sanity-check candidate counts**

Run: `uv run python -m src.cli --dry-run --score 2>&1 | tee /tmp/score_after.json | python -c "import json,sys; d=json.load(open('/tmp/score_after.json')); items=d['all_scored']; from collections import Counter; print(Counter(i['source'] for i in items if i['genre']=='paper')); print(Counter(i['source'] for i in items if i['genre']=='announcement'))"`
Expected: no crash; `hf-papers` item count reflects the `min_score: 15` filter having removed low-upvote papers, and `github_releases`-sourced (`*-gh` named) sources should show fewer canary/rc entries than before — exact numbers depend on live data, not asserted in a test, this is the manual sanity check per the spec's "dry-run smoke 对比过滤前后候选数". If `--dry-run --score` errors, check `src/cli.py`'s `run_dry_score` (or equivalent) for the exact JSON key names used above.

- [ ] **Step 12: Commit**

```bash
git add src/adapters/sources/github_releases.py src/adapters/sources/hf_papers.py config/sources.yaml tests/contract/test_github_releases_adapter.py tests/contract/test_hf_papers_adapter.py
git commit -m "feat(adapters): filter prerelease GitHub releases + hf-papers min_score floor

github_releases.py skips r.get('prerelease')==True (kills canary/rc/nightly
spam using GitHub's own signal, no regex needed). hf_papers.py gains the
same min_score gate hn.py already uses; config/sources.yaml sets
min_score: 15 on hf-papers based on real upvote distribution in
docs/recent-papers.md."
```

---

## Task 4: Cap total GitHub-sourced content in the final report

**Files:**
- Modify: `src/core/types.py` (add `adapter` field to `RawItem`, add `adapter_quota` field to `PublishConfig`)
- Modify: `src/core/config.py:151-163` (`load_publish_config` — load `adapter_quota`)
- Modify: `src/pipeline/collect.py:38-43` (`_run_one` — backfill `item.adapter` from `source.adapter`)
- Modify: `src/pipeline/score.py` (new `apply_adapter_quota` function, next to `apply_quota`)
- Modify: `src/pipeline/publish.py:16,77-97` (`build_report` — call `apply_adapter_quota` before `apply_quota`)
- Modify: `config/publish.yaml` (add `adapter_quota: {github_releases: 2, github_trending: 1}`)
- Test: `tests/contract/test_collect_unit.py` (adapter backfill test)
- Test: `tests/contract/test_score_unit.py` (new `apply_adapter_quota` tests)
- Test: `tests/contract/test_publish_config.py` (loader test)
- Test: `tests/golden/test_publish.py` (adapter cap + genre quota interaction)

**Interfaces:**
- Consumes: Task 2/3 changes are unrelated to this task and don't need to be re-verified, but the full suite from those tasks must still pass after this one.
- Produces: `RawItem.adapter: str | None = None` (inherited automatically by `NewsItem`/`ScoredItem`/`InterpretedItem`/`ReviewedItem` via the existing `**item.model_dump()` construction pattern used in `dedup.py`/`interpret.py` — no changes needed in those files). `apply_adapter_quota(scored: list[ScoredItem], adapter_quota: dict[str, int]) -> tuple[list[ScoredItem], dict[str, QuotaLine]]`. `PublishConfig.adapter_quota: dict[str, int]` (default `{}`).

- [ ] **Step 1: Write the failing test for `RawItem.adapter` backfill in collect.py**

In `tests/contract/test_collect_unit.py`, add after `test_one_source_failure_does_not_break_chain` (find the end of that test function first, then add below it):

```python
async def test_adapter_field_backfilled_from_source(monkeypatch, cfg):
    specs = [
        SourceSpec(
            name="vllm-gh",
            url="u",
            genre=Genre.announcement,
            publisher=Publisher.company,
            adapter="github_releases",
        )
    ]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(
        collect_mod, "ADAPTERS", {"github_releases": FakeOK([_item("vllm-gh", 2)])}
    )
    res = await collect_mod.collect(cfg, _ctx())
    assert len(res.items) == 1
    assert res.items[0].adapter == "github_releases"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contract/test_collect_unit.py -v -k adapter_field`
Expected: FAIL with `AttributeError: 'RawItem' object has no attribute 'adapter'` (field doesn't exist yet).

- [ ] **Step 3: Add `adapter` field to `RawItem`**

In `src/core/types.py`, inside `class RawItem(BaseModel):` (around line 27-38), add after `fetched_via`:

```python
    fetched_via: Literal["native", "firecrawl"] = "native"
    adapter: str | None = None  # 回填自 SourceSpec.adapter, 供下游按"采集渠道"分组(如 GitHub 封顶, spec §5)
    signals: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Backfill it in `collect.py`**

In `src/pipeline/collect.py`, replace lines 39-43:

```python
    adapter = ADAPTERS[source.adapter]
    try:
        async with sem:
            items = await asyncio.wait_for(
                adapter.fetch(source, ctx, config.timeout_s), timeout=config.timeout_s
            )
            items = [it.model_copy(update={"adapter": source.adapter}) for it in items]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/contract/test_collect_unit.py -v`
Expected: all tests in this file PASS (1 new + pre-existing, no regressions).

- [ ] **Step 6: Write the failing tests for `apply_adapter_quota`**

In `tests/contract/test_score_unit.py`, add near the existing `apply_quota` tests (after `test_apply_quota_does_not_dedupe_same_source_within_genre`, before the `# --- topic relevance tests ---` comment):

```python
def _scored_list_with_adapter(ctx, *specs):
    # specs: (title, link, source, genre, published, adapter)
    items = []
    for title, link, source, genre, published, adapter in specs:
        ni = _ni(title, link, source, genre, published)
        items.append(ni.model_copy(update={"adapter": adapter}))
    return compute_scores(items, {}, ScoringConfig(), ctx)


def test_apply_adapter_quota_trims_to_cap_keeping_top_scored():
    ctx = _ctx()
    fresh = NOW
    mid = NOW - timedelta(hours=36)
    stale = NOW - timedelta(hours=100)
    scored = _scored_list_with_adapter(
        ctx,
        ("r1", "https://gh/1", "s1", Genre.announcement, fresh, "github_releases"),
        ("r2", "https://gh/2", "s2", Genre.announcement, mid, "github_releases"),
        ("r3", "https://gh/3", "s3", Genre.announcement, stale, "github_releases"),
    )
    selected, report = apply_adapter_quota(scored, {"github_releases": 2})
    assert report["github_releases"].available == 3
    assert report["github_releases"].selected == 2
    links = {s.link for s in selected}
    assert links == {"https://gh/1", "https://gh/2"}  # stale (lowest score) dropped


def test_apply_adapter_quota_ignores_unlisted_adapters():
    ctx = _ctx()
    scored = _scored_list_with_adapter(
        ctx, ("a", "https://a/1", "s1", Genre.writeup, NOW, "rss")
    )
    selected, report = apply_adapter_quota(scored, {"github_releases": 2})
    assert len(selected) == 1  # "rss" not in adapter_quota -> not filtered
    assert "rss" not in report


def test_apply_adapter_quota_empty_dict_returns_unchanged():
    ctx = _ctx()
    scored = _scored_list_with_adapter(
        ctx, ("a", "https://a/1", "s1", Genre.writeup, NOW, "rss")
    )
    selected, report = apply_adapter_quota(scored, {})
    assert selected == scored
    assert report == {}
```

Add `apply_adapter_quota` to the existing import line at the top of the file:

```python
from src.pipeline.score import _topic_relevance, apply_adapter_quota, apply_quota, compute_scores, recency_band
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `pytest tests/contract/test_score_unit.py -v -k apply_adapter_quota`
Expected: FAIL with `ImportError: cannot import name 'apply_adapter_quota'` (function doesn't exist yet).

- [ ] **Step 8: Implement `apply_adapter_quota` in `score.py`**

In `src/pipeline/score.py`, add right after `apply_quota` (after line 175, before `def score(`):

```python
def apply_adapter_quota(
    scored: list[ScoredItem], adapter_quota: dict[str, int]
) -> tuple[list[ScoredItem], dict[str, QuotaLine]]:
    """按 item.adapter 分组截断(spec §5), 与 apply_quota 同构但分组键是采集渠道不是内容类型。
    adapter_quota 里没写的 adapter 完全不受限(通过型)。空 adapter_quota -> 原样返回。"""
    if not adapter_quota:
        return scored, {}

    by_adapter: dict[str, list[ScoredItem]] = defaultdict(list)
    passthrough: list[ScoredItem] = []
    for s in scored:
        key = s.adapter or ""
        if key in adapter_quota:
            by_adapter[key].append(s)
        else:
            passthrough.append(s)

    selected: list[ScoredItem] = list(passthrough)
    report: dict[str, QuotaLine] = {}
    for a, group in by_adapter.items():
        group_sorted = sorted(group, key=lambda s: (-s.score, s.published_at, s.link))
        q = adapter_quota.get(a, 0)
        take = group_sorted[:q]
        selected.extend(take)
        report[a] = QuotaLine(genre=a, available=len(group), quota=q, selected=len(take))

    selected.sort(key=lambda s: (-s.score, s.published_at, s.link))
    return selected, report
```

`defaultdict` and `QuotaLine` are already imported at the top of `score.py` (used by `apply_quota` right above) — no new imports needed.

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/contract/test_score_unit.py -v`
Expected: all tests in this file PASS (3 new + all pre-existing `apply_quota` tests, no regressions).

- [ ] **Step 10: Add `adapter_quota` to `PublishConfig` + loader**

In `src/core/types.py`, inside `class PublishConfig:` (around line 344-368), add after `genre_labels`:

```python
    genre_labels: dict[str, str] = field(
        default_factory=lambda: {
            "paper": "论文",
            "model": "模型",
            "announcement": "官方",
            "writeup": "博客 / 工具",
            "news": "新闻",
        }
    )
    adapter_quota: dict[str, int] = field(default_factory=dict)  # 按采集渠道封顶(spec §5), 不占用 genre 配额名额
```

In `src/core/config.py`, inside `load_publish_config` (around line 151-163), add:

```python
def load_publish_config(path: str) -> PublishConfig:
    """Load publish display constants from YAML; missing/empty file -> defaults."""
    data = _read_yaml(path)
    d = PublishConfig()
    return PublishConfig(
        must_read_count=data.get("must_read_count", d.must_read_count),
        top_keywords=data.get("top_keywords", d.top_keywords),
        pending_watermark=data.get("pending_watermark", d.pending_watermark),
        min_display_score=data.get("min_display_score", d.min_display_score),
        quota=data.get("quota", d.quota),
        total_limit=data.get("total_limit", d.total_limit),
        genre_labels=data.get("genre_labels", d.genre_labels),
        adapter_quota=data.get("adapter_quota", d.adapter_quota),
    )
```

- [ ] **Step 11: Write the failing loader test**

In `tests/contract/test_publish_config.py`, add:

```python
def test_load_publish_config_adapter_quota_default_empty():
    cfg = load_publish_config("does/not/exist.yaml")
    assert cfg.adapter_quota == {}


def test_load_publish_config_adapter_quota_override(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text(
        "adapter_quota: {github_releases: 2, github_trending: 1}\n", encoding="utf-8"
    )
    cfg = load_publish_config(str(p))
    assert cfg.adapter_quota == {"github_releases": 2, "github_trending": 1}
```

- [ ] **Step 12: Run test to verify it fails, then passes**

Run: `pytest tests/contract/test_publish_config.py -v -k adapter_quota`
Expected: FAILs first (`AttributeError: 'PublishConfig' object has no attribute 'adapter_quota'`) before Step 10, PASSes after. (Since Step 10 already applied the fix, run this now to confirm — should PASS immediately since the code change already happened. If it fails, double check Step 10 was applied correctly before proceeding.)

- [ ] **Step 13: Wire `apply_adapter_quota` into `publish.py`**

In `src/pipeline/publish.py`, update the import (line 16):

```python
from src.pipeline.score import apply_adapter_quota, apply_quota
```

Then update `build_report` (lines 86-87):

```python
    # 采集渠道封顶(spec §5): 先砍 GitHub 超额, 让 genre 配额的剩余名额优先给非 GitHub 条目
    items, _ = apply_adapter_quota(items, config.adapter_quota)
    # per-genre 配额 + total_limit: 人 keep 之后对 kept 集合施加(组成控制, 复用 score 纯函数)
    items, _ = apply_quota(items, config.quota, config.total_limit)
```

- [ ] **Step 14: Write the failing golden test for the interaction**

In `tests/golden/test_publish.py`, find the existing `_ri(...)` helper (already reviewed, builds `ReviewedItem`). Add a new test after the other `build_report`-related tests in that file (search for `def test_build_report` to find where they live, add alongside):

```python
def test_build_report_adapter_quota_applies_before_genre_quota():
    cfg = PublishConfig(
        quota={"announcement": 3},
        total_limit=99,
        adapter_quota={"github_releases": 1},
    )
    items = [
        _ri(link="https://gh/1", genre=Genre.announcement, score=90).model_copy(
            update={"adapter": "github_releases"}
        ),
        _ri(link="https://gh/2", genre=Genre.announcement, score=85).model_copy(
            update={"adapter": "github_releases"}
        ),
        _ri(link="https://openai/1", genre=Genre.announcement, score=70).model_copy(
            update={"adapter": "rss"}
        ),
    ]
    result = ReviewResult(
        reviewed_items=items,
        daily_take=None,
        input_count=3,
        kept_count=3,
        dropped_count=0,
        edited_count=0,
        is_reviewed=True,
        is_pending=False,
    )
    report = build_report(result, "2026-07-14", cfg)
    links = {it.link for sec in report.categories for it in sec.items}
    # github_releases capped to 1 (highest-scored: gh/1) -> frees a genre slot for the rss item
    assert links == {"https://gh/1", "https://openai/1"}
```

`ReviewResult`'s real fields (`src/core/types.py:309-317`) are `reviewed_items, daily_take, input_count, kept_count, dropped_count, edited_count, is_reviewed, is_pending` — all required, all supplied above.

- [ ] **Step 15: Run test to verify it fails, then passes**

Run: `pytest tests/golden/test_publish.py -v -k adapter_quota`
Expected: FAILs before Step 13 (all 3 items pass through, `gh/2` also present, `links` has 3 entries not 2) — but since Step 13 already happened, this should PASS now. Run it to confirm; if it fails, check `_ri`'s default `score` handling and that `model_copy` correctly sets `adapter` (pydantic's `model_copy(update=...)` is the standard way to override a field post-construction).

- [ ] **Step 16: Run full suite + lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: all green — this is the last task, so this run also confirms Tasks 1-3 didn't regress.

- [ ] **Step 17: Set the quota in `config/publish.yaml`**

In `config/publish.yaml`, add after the `quota:` line:

```yaml
adapter_quota: {github_releases: 2, github_trending: 1}  # 按采集渠道封顶, 不占用 genre 配额名额(spec §5)
```

- [ ] **Step 18: dry-run smoke to confirm final report caps**

Run: `uv run python -m src.cli --dry-run --publish 2>&1 | tee /tmp/publish_after.md`
Expected: no crash; the printed markdown's "官方" (announcement) and "博客 / 工具" (writeup) sections should show at most 2 `github_releases`-origin items and 1 `github_trending`-origin item respectively (manual visual check — item source names ending in `-gh` for releases, `gh-trending-ai` for trending — per spec's e2e test row; not asserted by pytest since it depends on live collected data, and note `--dry-run` doesn't run the human review/keep step so `build_report`'s quota logic may see a different item set than production — this is a smoke check of "does it run and look plausible," not a full production simulation).

- [ ] **Step 19: Commit**

```bash
git add src/core/types.py src/core/config.py src/pipeline/collect.py src/pipeline/score.py src/pipeline/publish.py config/publish.yaml tests/contract/test_collect_unit.py tests/contract/test_score_unit.py tests/contract/test_publish_config.py tests/golden/test_publish.py
git commit -m "feat(publish): cap github_releases/github_trending so they can't crowd out real announcements

RawItem.adapter (backfilled once in collect.py, propagates automatically
through the existing model_dump()-spread pattern in dedup/interpret) lets
a new apply_adapter_quota pure function (mirrors apply_quota, keyed by
adapter instead of genre) run before the existing genre quota in
build_report. config/publish.yaml sets adapter_quota:
{github_releases: 2, github_trending: 1}."
```

---

## Post-implementation: update KANBAN

**Files:**
- Modify: `docs/KANBAN.md` (already has a pointer to the spec at the P0 row for "主动降噪" — once this PR merges, flip that row's checkbox and update its detail text to reference this plan/PR instead of "待写实施计划")

- [ ] **Step 1: After the PR merges to master**, edit `docs/KANBAN.md`'s row for "主动降噪·Paper + GitHub Releases 重要性" — change `☐` to `☑` and move the row (or a summary of it) into §5 "✅ Done", referencing the PR number once known. This is a small doc-only follow-up, not part of the 4 code commits above — do it as its own commit after merge, not bundled into this feature branch.

---

## Self-Review Notes (already applied above, recorded for the reviewer)

- **Spec coverage**: §1→Task 2, §2→Task 1, §3+§4→Task 3, §5→Task 4. All 5 design sections have a task. The "不做" (YAGNI) list and the explicit cross-day-carryover exclusion from the spec are respected — no task implements them.
- **Type consistency checked**: `build_item_prompt` signature change (Task 2) has exactly 2 call sites total (1 production in `interpret_item`, 2 in tests) — all 3 updated in the same task. `apply_adapter_quota`'s signature/return type matches `apply_quota`'s shape (`tuple[list[ScoredItem], dict[str, QuotaLine]]`) so `publish.py` can call both the same way.
- **No placeholders**: every step shows exact code, exact file paths, exact test assertions.
