# Metrics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the KANBAN §3 P0 元任务 — every finalize run emits a per-day metrics JSON + PNG (matplotlib 2-subplot) + Hugo md page + TG photo message that surfaces funnel loss + fallback_rate + per-genre noise.

**Architecture:** New `src/pipeline/metrics.py` reads the seven jsonl artifacts under `data/runs/<latest>/`, computes funnel + rates + per-genre + per-source + trend_7d (from prior `content/metrics/*.json`). New `src/pipeline/metrics_render.py` renders those into PNG (matplotlib) + Hugo md + TG caption. `src/cli.py` gains `--tick metrics` running the pipeline; `.github/workflows/finalize.yml` appends the tick as its last step. `TelegramBot` gains a `send_photo` method.

**Tech Stack:** Python 3.12, matplotlib (already in `uv sync` transitively via other deps — verify + add if missing), pytest, `python-telegram-bot` (already used), Pydantic (existing).

## Global Constraints

(spec [`docs/superpowers/specs/2026-07-01-metrics-dashboard-design.md`](../specs/2026-07-01-metrics-dashboard-design.md))

- `date` in output JSON = **北京时间日期** (matches `content/posts/YYYY-MM-DD.md` naming); `generated_at` = UTC ISO 8601
- Output paths: `content/metrics/YYYY-MM-DD.{json,png,md}` (git-tracked, all three)
- PNG: 800 × 700 px, PNG-optimized, target ≤ 60 KB
- PNG has 2 subplots stacked vertically: (top) 7d fallback_rate + eligible_rate lines; (bottom) today waterfall bar (6 stages)
- No emoji in PNG axis labels/legend; TG caption has exactly one 📊 leading emoji
- CLI: `uv run python -m src.cli --tick metrics` (no other args); support `--dry-run` (skip TG, still write files)
- Metrics failure MUST NOT block finalize main pipeline (workflow step `continue-on-error: true`)
- `interpreted_fallback` = rows in `04_interpreted.jsonl` with `interpretation_status == "extractive_fallback"` (verified against `src/pipeline/interpret.py:105`)
- `posted` = rows in `05_reviewed.jsonl` with `review_action == "keep"` (there is no `06_published.jsonl`; verified against `src/core/types.py:278`)
- ruff clean, ruff format clean before every commit
- Follow existing pattern: pure functions in `src/pipeline/`, IO adapters in `src/adapters/` or `src/notifiers/`
- pytest-asyncio `asyncio_mode = "auto"` (do NOT add `@pytest.mark.asyncio` markers)

---

### Task 1: `metrics.py` — funnel + rates pure functions

**Files:**
- Create: `src/pipeline/metrics.py`
- Create: `tests/fixtures/metrics_run_dir/` (5 fixture jsonl files, see Step 1)
- Create: `tests/contract/test_metrics_compute.py`

**Interfaces:**
- Consumes: `Path` (stdlib), `json` (stdlib); no other project imports needed yet
- Produces:
  - `compute_funnel(run_dir: Path) -> dict[str, int]` — returns keys `candidates, after_dedup, after_score_quota, interpreted_ok, interpreted_fallback, review_eligible, posted`
  - `compute_rates(funnel: dict[str, int]) -> dict[str, float]` — returns `fallback_rate, dedup_reduction, quota_reduction, interpret_fail_rate, keep_rate`, all in `[0.0, 1.0]`; division-by-zero returns `0.0`

- [ ] **Step 1: Create fixture directory + 5 jsonl files**

Create the directory:
```bash
mkdir -p tests/fixtures/metrics_run_dir
```

Write `tests/fixtures/metrics_run_dir/01_source_reports.jsonl` (3 sources):

```jsonl
{"name":"hf-papers","status":"working","item_count":3,"elapsed_ms":120}
{"name":"the-decoder","status":"working","item_count":1,"elapsed_ms":80}
{"name":"github-releases","status":"working","item_count":1,"elapsed_ms":300}
```

Write `tests/fixtures/metrics_run_dir/01_collected.jsonl` (5 raw items, matches source_reports totals):

```jsonl
{"source":"hf-papers","title_en":"Paper A","link":"https://x/a","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"a","signals":{},"fetched_via":"native"}
{"source":"hf-papers","title_en":"Paper B","link":"https://x/b","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"b","signals":{},"fetched_via":"native"}
{"source":"hf-papers","title_en":"Paper C","link":"https://x/c","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"c","signals":{},"fetched_via":"native"}
{"source":"the-decoder","title_en":"News X","link":"https://x/nx","genre":"news","publisher":"media","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"nx","signals":{},"fetched_via":"native"}
{"source":"github-releases","title_en":"Release R","link":"https://x/r","genre":"release","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"r","signals":{},"fetched_via":"native"}
```

Write `tests/fixtures/metrics_run_dir/02_deduped.jsonl` (4 rows — 1 dedup drop):

```jsonl
{"source":"hf-papers","title_en":"Paper A","link":"https://x/a","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"a","signals":{},"fetched_via":"native"}
{"source":"hf-papers","title_en":"Paper B","link":"https://x/b","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"b","signals":{},"fetched_via":"native"}
{"source":"the-decoder","title_en":"News X","link":"https://x/nx","genre":"news","publisher":"media","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"nx","signals":{},"fetched_via":"native"}
{"source":"github-releases","title_en":"Release R","link":"https://x/r","genre":"release","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"r","signals":{},"fetched_via":"native"}
```

Write `tests/fixtures/metrics_run_dir/03_scored.jsonl` (2 rows — quota keeps top-2):

```jsonl
{"source":"the-decoder","title_en":"News X","link":"https://x/nx","genre":"news","publisher":"media","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"nx","signals":{},"fetched_via":"native","score":88}
{"source":"hf-papers","title_en":"Paper A","link":"https://x/a","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"a","signals":{},"fetched_via":"native","score":75}
```

Write `tests/fixtures/metrics_run_dir/04_interpreted.jsonl` (2 rows — 1 ok + 1 fallback):

```jsonl
{"source":"the-decoder","title_en":"News X","link":"https://x/nx","genre":"news","publisher":"media","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"nx","signals":{},"fetched_via":"native","score":88,"interpretation_status":"ok","eligible_for_must_read":true,"title":"新闻 X","body":"...","tags":[],"evidence":[]}
{"source":"hf-papers","title_en":"Paper A","link":"https://x/a","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"a","signals":{},"fetched_via":"native","score":75,"interpretation_status":"extractive_fallback","eligible_for_must_read":false,"title":"Paper A","body":"a","tags":[],"evidence":[]}
```

Write `tests/fixtures/metrics_run_dir/05_reviewed.jsonl` (2 rows — 1 keep + 1 edit; posted=1):

```jsonl
{"source":"the-decoder","title_en":"News X","link":"https://x/nx","genre":"news","publisher":"media","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"nx","signals":{},"fetched_via":"native","score":88,"interpretation_status":"ok","eligible_for_must_read":true,"title":"新闻 X","body":"...","tags":[],"evidence":[],"review_action":"keep"}
{"source":"hf-papers","title_en":"Paper A","link":"https://x/a","genre":"paper","publisher":"company","published_at":"2026-07-01T00:00:00+00:00","raw_summary":"a","signals":{},"fetched_via":"native","score":75,"interpretation_status":"extractive_fallback","eligible_for_must_read":false,"title":"Paper A","body":"a","tags":[],"evidence":[],"review_action":"edit"}
```

Fixture expected funnel: `candidates=5, after_dedup=4, after_score_quota=2, interpreted_ok=1, interpreted_fallback=1, review_eligible=2, posted=1`.

- [ ] **Step 2: Write failing test `tests/contract/test_metrics_compute.py`**

```python
from pathlib import Path

from src.pipeline.metrics import compute_funnel, compute_rates

FIXTURE = Path(__file__).parent.parent / "fixtures" / "metrics_run_dir"


def test_compute_funnel_from_fixture_run_dir():
    f = compute_funnel(FIXTURE)
    assert f == {
        "candidates": 5,
        "after_dedup": 4,
        "after_score_quota": 2,
        "interpreted_ok": 1,
        "interpreted_fallback": 1,
        "review_eligible": 2,
        "posted": 1,
    }


def test_compute_funnel_empty_run_dir_returns_zeros(tmp_path):
    # Empty run_dir → all zeros, no crash
    f = compute_funnel(tmp_path)
    assert f == {
        "candidates": 0,
        "after_dedup": 0,
        "after_score_quota": 0,
        "interpreted_ok": 0,
        "interpreted_fallback": 0,
        "review_eligible": 0,
        "posted": 0,
    }


def test_compute_rates_normal():
    funnel = {
        "candidates": 87,
        "after_dedup": 68,
        "after_score_quota": 24,
        "interpreted_ok": 21,
        "interpreted_fallback": 3,
        "review_eligible": 20,
        "posted": 12,
    }
    r = compute_rates(funnel)
    assert r["fallback_rate"] == 3 / 24
    assert r["dedup_reduction"] == 1 - 68 / 87
    assert r["quota_reduction"] == 1 - 24 / 68
    assert r["interpret_fail_rate"] == 3 / 24
    assert r["keep_rate"] == 12 / 20


def test_compute_rates_division_by_zero_returns_zero():
    r = compute_rates({
        "candidates": 0,
        "after_dedup": 0,
        "after_score_quota": 0,
        "interpreted_ok": 0,
        "interpreted_fallback": 0,
        "review_eligible": 0,
        "posted": 0,
    })
    assert r == {
        "fallback_rate": 0.0,
        "dedup_reduction": 0.0,
        "quota_reduction": 0.0,
        "interpret_fail_rate": 0.0,
        "keep_rate": 0.0,
    }
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/contract/test_metrics_compute.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.pipeline.metrics'`.

- [ ] **Step 4: Write `src/pipeline/metrics.py`**

```python
"""Pure metrics functions: read per-run jsonl → funnel counts + derived rates.

No IO other than reading the passed run_dir. No side effects.
Ships zero-safe: missing files → 0 counts; division by zero → 0.0 rates.
"""

from __future__ import annotations

import json
from pathlib import Path


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _count_matching(path: Path, key: str, value: str) -> int:
    if not path.is_file():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get(key) == value:
                n += 1
    return n


def compute_funnel(run_dir: Path) -> dict[str, int]:
    """Read the 5 relevant jsonl files under run_dir and return funnel counts."""
    interpreted = run_dir / "04_interpreted.jsonl"
    reviewed = run_dir / "05_reviewed.jsonl"
    return {
        "candidates": _count_lines(run_dir / "01_collected.jsonl"),
        "after_dedup": _count_lines(run_dir / "02_deduped.jsonl"),
        "after_score_quota": _count_lines(run_dir / "03_scored.jsonl"),
        "interpreted_ok": _count_matching(interpreted, "interpretation_status", "ok"),
        "interpreted_fallback": _count_matching(interpreted, "interpretation_status", "extractive_fallback"),
        "review_eligible": _count_lines(reviewed),
        "posted": _count_matching(reviewed, "review_action", "keep"),
    }


def _safe_ratio(num: float, denom: float) -> float:
    return num / denom if denom else 0.0


def compute_rates(funnel: dict[str, int]) -> dict[str, float]:
    interpreted_total = funnel["interpreted_ok"] + funnel["interpreted_fallback"]
    return {
        "fallback_rate": _safe_ratio(funnel["interpreted_fallback"], interpreted_total),
        "dedup_reduction": 1.0 - _safe_ratio(funnel["after_dedup"], funnel["candidates"]) if funnel["candidates"] else 0.0,
        "quota_reduction": 1.0 - _safe_ratio(funnel["after_score_quota"], funnel["after_dedup"]) if funnel["after_dedup"] else 0.0,
        "interpret_fail_rate": _safe_ratio(funnel["interpreted_fallback"], interpreted_total),
        "keep_rate": _safe_ratio(funnel["posted"], funnel["review_eligible"]),
    }
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/contract/test_metrics_compute.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/pipeline/metrics.py tests/contract/test_metrics_compute.py
uv run ruff format src/pipeline/metrics.py tests/contract/test_metrics_compute.py
git add src/pipeline/metrics.py tests/contract/test_metrics_compute.py tests/fixtures/metrics_run_dir/
git commit -m "feat(metrics): pure funnel + rates over run_dir jsonl artifacts"
```

---

### Task 2: `metrics.py` — per_genre, per_source_top10, samples, trend_7d

**Files:**
- Modify: `src/pipeline/metrics.py` (append 4 functions)
- Modify: `tests/contract/test_metrics_compute.py` (append tests)
- Create: `tests/fixtures/metrics_history/` (3 historical metrics.json files)

**Interfaces:**
- Consumes: functions from Task 1
- Produces:
  - `compute_per_genre(run_dir: Path) -> dict[str, dict[str, int | float]]` — per-genre `{candidates, posted, noise_ratio}`; reads `01_collected.jsonl` for candidates by genre and `05_reviewed.jsonl` (`review_action=="keep"`) for posted
  - `compute_per_source_top10(run_dir: Path) -> list[dict]` — per-source `{name, yield, kept, noise_ratio}`, sorted by yield desc, top 10; reads `01_source_reports.jsonl` for yield and `05_reviewed.jsonl` (keep) for kept
  - `load_fallback_titles(run_dir: Path, limit: int = 3) -> list[str]` — extracts `title_en` from `04_interpreted.jsonl` rows with `interpretation_status == "extractive_fallback"`, up to `limit`
  - `load_trend_7d(metrics_dir: Path, today: str) -> dict` — reads up to 7 `content/metrics/YYYY-MM-DD.json` files ending at `today` (inclusive), returns `{dates: [...], fallback_rate: [...], eligible_rate: [...]}` where `eligible_rate = posted / candidates`; missing days get value `None`

- [ ] **Step 1: Create history fixture directory + 3 JSON files**

```bash
mkdir -p tests/fixtures/metrics_history
```

Write `tests/fixtures/metrics_history/2026-06-28.json`:

```json
{"date":"2026-06-28","funnel":{"candidates":80,"posted":10,"interpreted_ok":18,"interpreted_fallback":2},"rates":{"fallback_rate":0.1}}
```

Write `tests/fixtures/metrics_history/2026-06-29.json`:

```json
{"date":"2026-06-29","funnel":{"candidates":90,"posted":13,"interpreted_ok":20,"interpreted_fallback":4},"rates":{"fallback_rate":0.167}}
```

Write `tests/fixtures/metrics_history/2026-07-01.json`:

```json
{"date":"2026-07-01","funnel":{"candidates":87,"posted":12,"interpreted_ok":21,"interpreted_fallback":3},"rates":{"fallback_rate":0.125}}
```

(Deliberately no `2026-06-30.json` to exercise the missing-day path.)

- [ ] **Step 2: Append failing tests to `tests/contract/test_metrics_compute.py`**

```python
from src.pipeline.metrics import (
    compute_per_genre,
    compute_per_source_top10,
    load_fallback_titles,
    load_trend_7d,
)

FIXTURE_HISTORY = Path(__file__).parent.parent / "fixtures" / "metrics_history"


def test_compute_per_genre_from_fixture():
    # Fixture has 3 papers + 1 news + 1 release in 01_collected; 1 news kept in 05
    g = compute_per_genre(FIXTURE)
    assert g["paper"]["candidates"] == 3
    assert g["paper"]["posted"] == 0
    assert g["paper"]["noise_ratio"] == 1.0
    assert g["news"]["candidates"] == 1
    assert g["news"]["posted"] == 1
    assert g["news"]["noise_ratio"] == 0.0
    assert g["release"]["candidates"] == 1
    assert g["release"]["posted"] == 0
    assert g["release"]["noise_ratio"] == 1.0


def test_compute_per_source_top10_sorted_by_yield():
    top = compute_per_source_top10(FIXTURE)
    assert top[0]["name"] == "hf-papers"
    assert top[0]["yield"] == 3
    assert top[0]["kept"] == 0
    assert top[0]["noise_ratio"] == 1.0
    assert top[1]["yield"] in (1,)  # tie between the-decoder and github-releases
    assert len(top) == 3


def test_load_fallback_titles_limits():
    titles = load_fallback_titles(FIXTURE, limit=5)
    assert titles == ["Paper A"]  # only 1 fallback in fixture; limit doesn't force padding
    assert load_fallback_titles(FIXTURE, limit=0) == []


def test_load_trend_7d_fills_missing_with_none():
    trend = load_trend_7d(FIXTURE_HISTORY, "2026-07-01")
    assert trend["dates"] == [
        "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28",
        "2026-06-29", "2026-06-30", "2026-07-01",
    ]
    # 2026-06-25/26/27/30 missing → None
    assert trend["fallback_rate"] == [None, None, None, 0.1, 0.167, None, 0.125]
    # eligible_rate = posted / candidates
    assert trend["eligible_rate"][3] == 10 / 80
    assert trend["eligible_rate"][4] == 13 / 90
    assert trend["eligible_rate"][6] == 12 / 87
    assert trend["eligible_rate"][0] is None
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/contract/test_metrics_compute.py -v
```

Expected: 4 new tests FAIL with `ImportError`, prior 4 still pass.

- [ ] **Step 4: Append 4 functions to `src/pipeline/metrics.py`**

```python
from collections import Counter, defaultdict
from datetime import date, timedelta


def _iter_rows(path: Path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def compute_per_genre(run_dir: Path) -> dict[str, dict[str, int | float]]:
    """Group candidates + posted counts by genre; compute noise_ratio = 1 - posted/candidates."""
    collected = Counter(row.get("genre", "unknown") for row in _iter_rows(run_dir / "01_collected.jsonl"))
    posted = Counter(
        row.get("genre", "unknown")
        for row in _iter_rows(run_dir / "05_reviewed.jsonl")
        if row.get("review_action") == "keep"
    )
    out: dict[str, dict[str, int | float]] = {}
    for genre, cand in collected.items():
        p = posted.get(genre, 0)
        out[genre] = {
            "candidates": cand,
            "posted": p,
            "noise_ratio": 1.0 - (p / cand) if cand else 0.0,
        }
    return out


def compute_per_source_top10(run_dir: Path) -> list[dict]:
    """Top 10 sources by yield (from source_reports), with kept count from reviewed."""
    source_yield: dict[str, int] = {}
    for row in _iter_rows(run_dir / "01_source_reports.jsonl"):
        name = row.get("name")
        if name:
            source_yield[name] = int(row.get("item_count", 0))

    kept = Counter(
        row.get("source")
        for row in _iter_rows(run_dir / "05_reviewed.jsonl")
        if row.get("review_action") == "keep"
    )

    rows = []
    for name, y in source_yield.items():
        k = int(kept.get(name, 0))
        rows.append({
            "name": name,
            "yield": y,
            "kept": k,
            "noise_ratio": 1.0 - (k / y) if y else 0.0,
        })
    rows.sort(key=lambda r: r["yield"], reverse=True)
    return rows[:10]


def load_fallback_titles(run_dir: Path, limit: int = 3) -> list[str]:
    """Titles from interpret rows where interpretation_status == 'extractive_fallback'."""
    titles: list[str] = []
    for row in _iter_rows(run_dir / "04_interpreted.jsonl"):
        if row.get("interpretation_status") == "extractive_fallback":
            title = row.get("title_en") or row.get("title") or ""
            if title:
                titles.append(title)
            if len(titles) >= limit:
                break
    return titles


def load_trend_7d(metrics_dir: Path, today: str) -> dict:
    """Return per-day fallback_rate + eligible_rate for the 7 days ending at today (YYYY-MM-DD).

    Missing days → None. eligible_rate = posted / candidates.
    """
    today_d = date.fromisoformat(today)
    dates = [(today_d - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    fallback_rate: list[float | None] = []
    eligible_rate: list[float | None] = []

    for d in dates:
        p = metrics_dir / f"{d}.json"
        if not p.is_file():
            fallback_rate.append(None)
            eligible_rate.append(None)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            fallback_rate.append(None)
            eligible_rate.append(None)
            continue
        rates = data.get("rates") or {}
        funnel = data.get("funnel") or {}
        fallback_rate.append(rates.get("fallback_rate"))
        candidates = funnel.get("candidates") or 0
        posted = funnel.get("posted") or 0
        eligible_rate.append((posted / candidates) if candidates else None)

    return {"dates": dates, "fallback_rate": fallback_rate, "eligible_rate": eligible_rate}
```

- [ ] **Step 5: Run test to verify passes**

```bash
uv run pytest tests/contract/test_metrics_compute.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/pipeline/metrics.py tests/contract/test_metrics_compute.py
uv run ruff format src/pipeline/metrics.py tests/contract/test_metrics_compute.py
git add src/pipeline/metrics.py tests/contract/test_metrics_compute.py tests/fixtures/metrics_history/
git commit -m "feat(metrics): per_genre + per_source_top10 + fallback_titles + trend_7d"
```

---

### Task 3: `metrics_render.py` — PNG (matplotlib 2-subplot)

**Files:**
- Create: `src/pipeline/metrics_render.py`
- Create: `tests/contract/test_metrics_render.py`
- Modify: `pyproject.toml` (add `matplotlib>=3.9` to dependencies if not already there)

**Interfaces:**
- Consumes: JSON structure from Task 1+2 (`{funnel, rates, per_genre, per_source_top10, samples, trend_7d}`)
- Produces:
  - `render_png(data: dict, out_path: Path) -> None` — writes PNG file 800×700 with 2 subplots stacked (top: trend lines; bottom: waterfall bar)

- [ ] **Step 1: Add matplotlib to dependencies (if missing)**

Check first:
```bash
grep -c matplotlib pyproject.toml
```

If output is `0`, add to `dependencies` list in `pyproject.toml`. Locate the `dependencies = [...]` block and append (matching existing quoting/style):

```toml
    "matplotlib>=3.9",
```

Then:
```bash
uv sync
```

If already present (output ≥ 1): skip the edit, still run `uv sync` to confirm resolvable.

- [ ] **Step 2: Write failing test `tests/contract/test_metrics_render.py`**

```python
from pathlib import Path

from src.pipeline.metrics_render import render_png

SAMPLE_DATA = {
    "date": "2026-07-01",
    "funnel": {
        "candidates": 87, "after_dedup": 68, "after_score_quota": 24,
        "interpreted_ok": 21, "interpreted_fallback": 3,
        "review_eligible": 20, "posted": 12,
    },
    "rates": {
        "fallback_rate": 0.125, "dedup_reduction": 0.218,
        "quota_reduction": 0.647, "interpret_fail_rate": 0.125, "keep_rate": 0.6,
    },
    "per_genre": {},
    "per_source_top10": [],
    "samples": {"fallback_titles": []},
    "trend_7d": {
        "dates": ["2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28",
                  "2026-06-29", "2026-06-30", "2026-07-01"],
        "fallback_rate": [0.15, 0.18, 0.11, 0.14, 0.20, 0.16, 0.125],
        "eligible_rate": [0.12, 0.10, 0.15, 0.13, 0.08, 0.14, 0.138],
    },
}


def test_render_png_writes_valid_png_at_expected_size(tmp_path):
    out = tmp_path / "test.png"
    render_png(SAMPLE_DATA, out)
    assert out.is_file()
    data = out.read_bytes()
    # PNG magic header
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # Reasonable size (spec target ≤ 60 KB; guard against runaway)
    assert 5_000 < len(data) < 200_000


def test_render_png_handles_missing_trend_gracefully(tmp_path):
    d = {**SAMPLE_DATA, "trend_7d": {"dates": SAMPLE_DATA["trend_7d"]["dates"],
                                     "fallback_rate": [None] * 7,
                                     "eligible_rate": [None] * 7}}
    out = tmp_path / "test2.png"
    render_png(d, out)  # must not raise
    assert out.is_file()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/contract/test_metrics_render.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write `src/pipeline/metrics_render.py`**

```python
"""Render metrics dict → PNG (matplotlib 2 subplot: 7d trend + today waterfall)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

_FUNNEL_STAGES: list[tuple[str, str]] = [
    ("candidates", "候选"),
    ("after_dedup", "去重后"),
    ("after_score_quota", "配额后"),
    ("interpreted_ok", "解读OK"),
    ("review_eligible", "审校后"),
    ("posted", "已发布"),
]


def _plot_trend(ax: Any, trend: dict) -> None:
    dates = trend.get("dates", [])
    fb = trend.get("fallback_rate", [])
    el = trend.get("eligible_rate", [])
    xs = list(range(len(dates)))

    def _drop_none(pairs):
        return [(x, y) for x, y in pairs if y is not None]

    fb_pts = _drop_none(zip(xs, fb))
    el_pts = _drop_none(zip(xs, el))
    if fb_pts:
        fx, fy = zip(*fb_pts)
        ax.plot(fx, fy, color="#d62728", marker="o", label="fallback_rate")
    if el_pts:
        ex, ey = zip(*el_pts)
        ax.plot(ex, ey, color="#2ca02c", marker="o", label="eligible_rate")

    ax.set_xticks(xs)
    ax.set_xticklabels([d[5:] for d in dates], rotation=0, fontsize=8)
    ax.set_ylim(0, 1)
    ax.set_ylabel("rate")
    ax.set_title("7d trend (fallback + eligible)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)


def _plot_waterfall(ax: Any, funnel: dict) -> None:
    labels = [label for _, label in _FUNNEL_STAGES]
    values = [funnel.get(key, 0) for key, _ in _FUNNEL_STAGES]
    colors = ["#888888", "#a6cee3", "#fdbf6f", "#ffff99", "#b2df8a", "#33a02c"]

    y_positions = list(range(len(labels)))
    ax.barh(y_positions, values, color=colors)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()  # top-to-bottom flow
    ax.set_xlabel("items")
    ax.set_title(f"today funnel (2026-07-01)".replace("2026-07-01", ""))

    # Annotate each bar: value + delta from prior stage
    prev = None
    for i, (val, label) in enumerate(zip(values, labels)):
        text = str(val)
        if prev is not None and prev > 0:
            delta = val - prev
            pct = 100.0 * delta / prev
            text += f"  ({delta:+d}, {pct:+.1f}%)"
        ax.text(val + max(values, default=0) * 0.01, i, text, va="center", fontsize=8)
        prev = val


def render_png(data: dict, out_path: Path) -> None:
    """Write metrics PNG to out_path. 800×700 px, 2 subplots stacked."""
    fig, (ax_trend, ax_funnel) = plt.subplots(
        2, 1, figsize=(8.0, 7.0), dpi=100, gridspec_kw={"height_ratios": [1, 1.5]}
    )
    _plot_trend(ax_trend, data.get("trend_7d") or {})
    ax_funnel_title = f"today funnel ({data.get('date', '')})"
    _plot_waterfall(ax_funnel, data.get("funnel") or {})
    ax_funnel.set_title(ax_funnel_title)
    fig.tight_layout()
    fig.savefig(out_path, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
```

- [ ] **Step 5: Run test to verify passes**

```bash
uv run pytest tests/contract/test_metrics_render.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/pipeline/metrics_render.py tests/contract/test_metrics_render.py
uv run ruff format src/pipeline/metrics_render.py tests/contract/test_metrics_render.py
git add src/pipeline/metrics_render.py tests/contract/test_metrics_render.py pyproject.toml uv.lock
git commit -m "feat(metrics): render_png with matplotlib 2-subplot (7d trend + waterfall)"
```

---

### Task 4: `metrics_render.py` — Hugo md + TG caption

**Files:**
- Modify: `src/pipeline/metrics_render.py` (append 2 functions)
- Modify: `tests/contract/test_metrics_render.py` (append tests)

**Interfaces:**
- Consumes: same dict as Task 3
- Produces:
  - `render_md(data: dict) -> str` — Hugo-style markdown with front-matter, embedded PNG link, per-genre table, fallback samples
  - `render_caption(data: dict, site_base_url: str = "https://ai-newsday.github.io/core") -> str` — plain-text TG caption (single 📊, HTML mode compatible, includes link to Hugo page)

- [ ] **Step 1: Append failing tests**

Append to `tests/contract/test_metrics_render.py`:

```python
from src.pipeline.metrics_render import render_caption, render_md

SAMPLE_DATA_FULL = {
    **SAMPLE_DATA,
    "per_genre": {
        "paper": {"candidates": 32, "posted": 1, "noise_ratio": 0.969},
        "news": {"candidates": 12, "posted": 3, "noise_ratio": 0.75},
    },
    "per_source_top10": [
        {"name": "hf-papers", "yield": 30, "kept": 1, "noise_ratio": 0.967},
    ],
    "samples": {"fallback_titles": ["Paper A", "Release B"]},
}


def test_render_md_has_front_matter_and_key_fields():
    md = render_md(SAMPLE_DATA_FULL)
    assert md.startswith("---\n")
    assert 'title: "Metrics 2026-07-01"' in md
    assert "type: metrics" in md
    assert "draft: false" in md
    # Embed png relative to same directory
    assert "![funnel](./2026-07-01.png)" in md
    # Core numbers surfaced
    assert "87" in md and "12" in md and "12.5%" in md
    # per-genre table
    assert "paper" in md and "96.9%" in md
    # fallback sample
    assert "Paper A" in md
    # link to raw json
    assert "[原始 JSON](./2026-07-01.json)" in md


def test_render_caption_single_emoji_and_html_link():
    cap = render_caption(SAMPLE_DATA_FULL)
    # Exactly one 📊
    assert cap.count("📊") == 1
    assert "候选 87" in cap
    assert "合格 12" in cap
    assert "fallback 3" in cap
    assert 'href="https://ai-newsday.github.io/core/metrics/2026-07-01/"' in cap
    # No 警告/警号 emoji (spec says single emoji only)
    for banned in ("⚠️", "🔊", "❌", "✅"):
        assert banned not in cap
```

- [ ] **Step 2: RED**

```bash
uv run pytest tests/contract/test_metrics_render.py -v
```

Expected: 2 new tests FAIL with ImportError; prior 2 still pass.

- [ ] **Step 3: Append to `src/pipeline/metrics_render.py`**

```python
def render_md(data: dict) -> str:
    date_str = data.get("date", "")
    funnel = data.get("funnel") or {}
    rates = data.get("rates") or {}
    per_genre = data.get("per_genre") or {}
    samples = data.get("samples") or {}

    candidates = funnel.get("candidates", 0)
    posted = funnel.get("posted", 0)
    eligible_rate = (100.0 * posted / candidates) if candidates else 0.0
    fallback_pct = 100.0 * rates.get("fallback_rate", 0.0)

    # Identify the largest-loss stage in the funnel
    largest = _largest_loss_stage(funnel)

    lines: list[str] = [
        "---",
        f'title: "Metrics {date_str}"',
        f"date: {date_str}T09:15:00+08:00",
        "type: metrics",
        "draft: false",
        "---",
        "",
        f"![funnel](./{date_str}.png)",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 候选 (candidates) | {candidates} |",
        f"| 合格 (posted) | {posted} |",
        f"| 合格率 | {eligible_rate:.1f}% |",
        f"| fallback_rate (翻译 KPI) | {fallback_pct:.1f}% |",
        f"| 最大损失层 | {largest} |",
        "",
        "## per-genre 噪声比",
        "",
        "| genre | candidates | posted | 噪声比 |",
        "|---|---|---|---|",
    ]
    for genre, stats in sorted(per_genre.items(), key=lambda kv: kv[1].get("candidates", 0), reverse=True):
        noise = 100.0 * stats.get("noise_ratio", 0.0)
        lines.append(f"| {genre} | {stats.get('candidates', 0)} | {stats.get('posted', 0)} | {noise:.1f}% |")

    lines += ["", "## fallback 样本 (翻译失效的 title)", ""]
    for t in samples.get("fallback_titles") or []:
        lines.append(f"- {t}")

    lines += ["", f"[原始 JSON](./{date_str}.json)", ""]
    return "\n".join(lines)


def _largest_loss_stage(funnel: dict) -> str:
    """Return a human label like 'quota (掉 64.7%)' for the funnel stage with biggest drop."""
    stages = [
        ("dedup", "candidates", "after_dedup"),
        ("quota", "after_dedup", "after_score_quota"),
        ("interp", "after_score_quota", "interpreted_ok"),
        ("review", "interpreted_ok", "review_eligible"),
        ("human", "review_eligible", "posted"),
    ]
    best_label = "(none)"
    best_pct = 0.0
    for label, prev_key, next_key in stages:
        prev = funnel.get(prev_key, 0)
        nxt = funnel.get(next_key, 0)
        if prev <= 0:
            continue
        drop_pct = 100.0 * (prev - nxt) / prev
        if drop_pct > best_pct:
            best_pct = drop_pct
            best_label = f"{label} (掉 {drop_pct:.1f}%)"
    return best_label


def render_caption(data: dict, site_base_url: str = "https://ai-newsday.github.io/core") -> str:
    date_str = data.get("date", "")
    funnel = data.get("funnel") or {}
    rates = data.get("rates") or {}
    top_sources = data.get("per_source_top10") or []

    candidates = funnel.get("candidates", 0)
    posted = funnel.get("posted", 0)
    fallback_count = funnel.get("interpreted_fallback", 0)
    eligible_rate = (100.0 * posted / candidates) if candidates else 0.0
    fallback_pct = 100.0 * rates.get("fallback_rate", 0.0)
    largest = _largest_loss_stage(funnel)

    top_source_line = ""
    if top_sources:
        top = top_sources[0]
        noise_pct = 100.0 * top.get("noise_ratio", 0.0)
        top_source_line = f"top 噪源 {top['name']}: {top['yield']}→{top['kept']} ({noise_pct:.1f}%)"

    lines = [
        f"📊 metrics {date_str}",
        "",
        f"候选 {candidates} → 合格 {posted} ({eligible_rate:.1f}%)",
        f"fallback {fallback_count} ({fallback_pct:.1f}%)  ← 翻译 KPI",
        f"最大损失: {largest}",
    ]
    if top_source_line:
        lines.append(top_source_line)

    url = f"{site_base_url}/metrics/{date_str}/"
    lines += ["", f'<a href="{url}">详情</a>']
    return "\n".join(lines)
```

- [ ] **Step 4: GREEN**

```bash
uv run pytest tests/contract/test_metrics_render.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/pipeline/metrics_render.py tests/contract/test_metrics_render.py
uv run ruff format src/pipeline/metrics_render.py tests/contract/test_metrics_render.py
git add src/pipeline/metrics_render.py tests/contract/test_metrics_render.py
git commit -m "feat(metrics): render_md + render_caption with single-emoji TG output"
```

---

### Task 5: `TelegramBot.send_photo`

**Files:**
- Modify: `src/notifiers/telegram_polling.py` (add method to existing class)
- Create: `tests/contract/test_telegram_send_photo.py`

**Interfaces:**
- Consumes: `python-telegram-bot` `Bot` class
- Produces: on the existing bot notifier class (implementer must locate the class in `src/notifiers/telegram_polling.py`; look for `self._bot = Bot(token=...)` around line 68):
  - `async def send_photo(self, photo_path: Path, caption: str) -> None` — sends the file at `photo_path` as a photo with the given caption (`parse_mode="HTML"`). Uses the same `chat_id` config as `send_message`.

- [ ] **Step 1: Locate the bot class**

```bash
grep -n "class.*Notifier\|class.*Bot\|self._bot" src/notifiers/telegram_polling.py | head -10
```

Note the class name (likely `TelegramNotifier` or similar). Use it verbatim below.

- [ ] **Step 2: Write failing test `tests/contract/test_telegram_send_photo.py`**

Replace `<TelegramNotifierClass>` in the imports below with the class name discovered in Step 1.

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.notifiers.telegram_polling import <TelegramNotifierClass>


@pytest.fixture
def notifier():
    n = <TelegramNotifierClass>.__new__(<TelegramNotifierClass>)
    n._bot = MagicMock()
    n._bot.send_photo = AsyncMock()
    n._chat_id = "12345"
    return n


async def test_send_photo_calls_bot_with_file_and_caption(notifier, tmp_path):
    p = tmp_path / "test.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")
    await notifier.send_photo(p, "hello 📊")
    notifier._bot.send_photo.assert_awaited_once()
    call = notifier._bot.send_photo.await_args
    assert call.kwargs["chat_id"] == "12345"
    assert call.kwargs["caption"] == "hello 📊"
    assert call.kwargs["parse_mode"] == "HTML"
    assert call.kwargs["photo"] is not None
```

If the notifier class stores `_chat_id` under a different attribute name (e.g. `config.chat_id`), adjust the fixture and assertion to match. The point is: the method calls the bot's `send_photo` with the config's chat_id + the file + the caption + HTML parse mode.

- [ ] **Step 3: RED**

```bash
uv run pytest tests/contract/test_telegram_send_photo.py -v
```

Expected: FAIL — method not defined.

- [ ] **Step 4: Add `send_photo` method to the notifier class**

Locate the notifier class (from Step 1) and add this method next to the existing `send_message` method. Adjust `self._chat_id` reference to match the class's actual attribute (grep `send_message` internals for how it accesses the chat_id).

```python
async def send_photo(self, photo_path: Path, caption: str) -> None:
    """Send a photo file to the same chat as send_message uses. HTML parse mode."""
    with photo_path.open("rb") as f:
        await self._bot.send_photo(
            chat_id=self._chat_id,
            photo=f,
            caption=caption,
            parse_mode="HTML",
        )
```

Also add near the top of the file if not already imported:
```python
from pathlib import Path
```

- [ ] **Step 5: GREEN**

```bash
uv run pytest tests/contract/test_telegram_send_photo.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/notifiers/telegram_polling.py tests/contract/test_telegram_send_photo.py
uv run ruff format src/notifiers/telegram_polling.py tests/contract/test_telegram_send_photo.py
git add src/notifiers/telegram_polling.py tests/contract/test_telegram_send_photo.py
git commit -m "feat(notifiers): TelegramNotifier.send_photo for image + caption"
```

---

### Task 6: `--tick metrics` CLI + workflow wire + smoke

**Files:**
- Modify: `src/cli.py` (add `run_metrics` function + register in `--tick` dispatch)
- Modify: `.github/workflows/finalize.yml` (append `--tick metrics` step)

**Interfaces:**
- Consumes: everything above
- Produces:
  - `python -m src.cli --tick metrics` produces 3 files in `content/metrics/YYYY-MM-DD.*` and, if `TELEGRAM_BOT_TOKEN` is set, sends TG photo
  - `python -m src.cli --tick metrics --dry-run` writes files but does NOT send TG

- [ ] **Step 1: Locate the CLI dispatch**

```bash
grep -n "tick\|--tick\|argparse\|click" src/cli.py | head -20
```

Understand the existing dispatch pattern (argparse vs click). Also note how existing `--tick finalize` finds `data/runs/`.

- [ ] **Step 2: Add `run_metrics` function to `src/cli.py`**

Add near the other tick functions (`run_dry_score`, etc.). Wire it into the `--tick` argument dispatch alongside `collect` / `finalize`.

```python
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.pipeline.metrics import (
    compute_funnel,
    compute_per_genre,
    compute_per_source_top10,
    compute_rates,
    load_fallback_titles,
    load_trend_7d,
)
from src.pipeline.metrics_render import render_caption, render_md, render_png


def _latest_run_dir(base: Path = Path("data/runs")) -> Path | None:
    if not base.is_dir():
        return None
    runs = [p for p in base.iterdir() if p.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime)


def _beijing_date_today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def run_metrics(*, dry_run: bool = False, out_dir: Path = Path("content/metrics")) -> int:
    """Compute + render + (optionally) TG-send today's metrics. Returns exit code (0=ok)."""
    logger = logging.getLogger("metrics")
    latest = _latest_run_dir()
    if latest is None:
        logger.warning("no data/runs/<uuid>/ found; skipping metrics")
        return 0

    date_str = _beijing_date_today()
    funnel = compute_funnel(latest)
    rates = compute_rates(funnel)
    per_genre = compute_per_genre(latest)
    per_source_top10 = compute_per_source_top10(latest)
    samples = {"fallback_titles": load_fallback_titles(latest, limit=3)}
    trend_7d = load_trend_7d(out_dir, date_str)

    data = {
        "date": date_str,
        "run_id": latest.name,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "funnel": funnel,
        "rates": rates,
        "per_genre": per_genre,
        "per_source_top10": per_source_top10,
        "samples": samples,
        "trend_7d": trend_7d,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{date_str}.json"
    png_path = out_dir / f"{date_str}.png"
    md_path = out_dir / f"{date_str}.md"

    import json as _json
    json_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        render_png(data, png_path)
    except Exception as e:  # noqa: BLE001
        logger.error("render_png failed: %s", e)

    md_path.write_text(render_md(data), encoding="utf-8")

    if dry_run:
        logger.info("metrics dry-run: files written; skipping TG send")
        return 0

    # TG send (only if configured and png exists)
    import os
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        logger.warning("TELEGRAM_BOT_TOKEN not set; skipping TG send")
        return 0
    if not png_path.is_file():
        logger.warning("png missing; skipping TG send")
        return 0

    from src.notifiers.telegram_polling import TelegramConfig, TelegramNotifier  # adjust names if needed

    async def _send():
        config = TelegramConfig(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
        )
        notifier = TelegramNotifier(config)
        await notifier.send_photo(png_path, render_caption(data))

    try:
        asyncio.run(_send())
    except Exception as e:  # noqa: BLE001
        logger.error("TG send_photo failed: %s", e)
    return 0
```

Wire into the `--tick` dispatch. Locate the existing tick handler (likely a match/if-else on the `--tick` value near the bottom of `src/cli.py`) and add:

```python
elif args.tick == "metrics":
    return run_metrics(dry_run=args.dry_run)
```

If the class names or constructor signatures inside `TelegramConfig` / `TelegramNotifier` differ from what's guessed above, the implementer must adjust based on `grep 'class Telegram' src/notifiers/telegram_polling.py` findings from Task 5.

- [ ] **Step 3: Local smoke test**

Use one of the existing `data/runs/*` directories:

```bash
uv run python -m src.cli --tick metrics --dry-run
ls content/metrics/
```

Expected: `content/metrics/<today-beijing>.{json,png,md}` all exist. No TG send occurred.

Inspect the outputs:
```bash
cat content/metrics/*.json | head -50
file content/metrics/*.png
head -30 content/metrics/*.md
```

Expected: JSON has all keys, PNG is valid image, md has front-matter.

- [ ] **Step 4: Modify `.github/workflows/finalize.yml`**

Read the current file:
```bash
cat .github/workflows/finalize.yml
```

Locate the `- name: Run finalize tick` step. Immediately after it (before the "Commit draft" step), add:

```yaml
      - name: Run metrics tick
        continue-on-error: true
        run: uv run python -m src.cli --tick metrics
```

Then update the "Commit draft" step to also add `content/metrics/` before pushing. Find the line `git add content/` — leave it as-is (it already covers `content/metrics/`). Update the commit message to reflect both:

```yaml
      - name: Commit draft
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add content/
          git diff --cached --quiet || git commit -m "chore: finalize draft + metrics $(date -u +%Y-%m-%d)"
          git push
```

- [ ] **Step 5: Full suite green + lint**

```bash
uv run pytest tests/ -q
uv run ruff check .
uv run ruff format --check .
```

Expected: all green. If any prior test broke (unlikely — everything is additive), fix.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py .github/workflows/finalize.yml
git commit -m "feat(cli): --tick metrics; finalize.yml runs it non-fatally after finalize"
```

- [ ] **Step 7: Push + open PR**

```bash
git push -u origin <branch-name>
gh pr create --title "feat(metrics): daily dashboard — JSON + PNG + Hugo md + TG photo" --body "$(cat <<'EOF'
## Summary

Ships the KANBAN §3 P0 元任务. Every finalize run now emits:

- `content/metrics/YYYY-MM-DD.json` (funnel + rates + per_genre + per_source_top10 + samples + trend_7d)
- `content/metrics/YYYY-MM-DD.png` (matplotlib 2-subplot: 7d trend line + today funnel waterfall)
- `content/metrics/YYYY-MM-DD.md` (Hugo page: front-matter + png embed + tables)
- TG photo + caption (📊 metrics + core numbers + link to Hugo page)

Spec: [docs/superpowers/specs/2026-07-01-metrics-dashboard-design.md](docs/superpowers/specs/2026-07-01-metrics-dashboard-design.md)
Plan: [docs/superpowers/plans/2026-07-01-metrics-dashboard.md](docs/superpowers/plans/2026-07-01-metrics-dashboard.md)

## Test plan

- [x] `uv run pytest tests/` full suite green (X new contract tests)
- [x] `uv run ruff check .` clean
- [x] `uv run ruff format --check .` clean
- [x] `uv run python -m src.cli --tick metrics --dry-run` produces 3 files locally
- [ ] After merge: next finalize cron writes `content/metrics/2026-07-02.*` and sends TG photo

🤖 Generated with Claude Code
EOF
)"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| §"数据流" (finalize.yml → tick metrics) | Task 6 |
| §"落盘布局" (json/png/md in content/metrics/) | Task 6 (run_metrics + workflow) |
| §"JSON schema" (funnel/rates/per_genre/per_source/samples/trend_7d) | Tasks 1 + 2 + 6 (assembly in run_metrics) |
| §"PNG 图" (800×700, 2 subplot, no emoji, colors specified) | Task 3 |
| §"Hugo .md 页面" (front-matter + png embed + tables) | Task 4 (render_md) |
| §"TG 消息" (send_photo + single-emoji caption + HTML link) | Tasks 4 (render_caption) + 5 (send_photo method) + 6 (wiring) |
| §"`--tick metrics` CLI" | Task 6 |
| §"失败降级" (empty run_dir / json corrupted / matplotlib fails / TG fails / Hugo fails) | Task 1 (missing files → 0), Task 2 (JSON errors → None), Task 6 (wraps render_png + TG send in try/except; `continue-on-error: true` on workflow) |
| §"date 北京时间" (JSON date field vs generated_at UTC) | Task 6 (`_beijing_date_today`, `datetime.now(timezone.utc)`) |
| No emoji in PNG axis labels | Task 3 (labels are 候选/去重后/... no emoji) |
| Exactly one 📊 in TG caption | Task 4 (`test_render_caption_single_emoji_and_html_link`) |
| Failure MUST NOT block finalize | Task 6 (`continue-on-error: true`) |

**2. Placeholder scan:** No TBD / TODO / vague "add error handling". Every code block is complete. Fixture jsonl is verbatim; test assertions cite exact numbers.

**3. Type consistency:**
- `compute_funnel(run_dir: Path) -> dict[str, int]` — used identically in Task 6.
- `compute_rates(funnel: dict[str, int]) -> dict[str, float]` — used identically in Task 6.
- `load_trend_7d(metrics_dir: Path, today: str)` — used identically in Task 6 with `out_dir` (`content/metrics`) as `metrics_dir`.
- `render_png(data: dict, out_path: Path)` — consistent between Task 3 impl, Task 3 tests, Task 6 caller.
- `render_md(data: dict) -> str` — consistent.
- `render_caption(data: dict, site_base_url: str = ...)` — Task 6 uses default; tests use default; consistent.
- `TelegramNotifier.send_photo(photo_path: Path, caption: str)` — Task 5 defines; Task 6 calls.

**4. External-lookup steps flagged:**
- Task 5 Step 1 (locate notifier class name) and Task 6 Step 1 (locate CLI dispatch pattern) are the only two places the implementer must peek at existing code. Both are flagged with `grep` commands and expected outputs; both note fallback edits if naming differs.

## Not in this PR (deferred)

- `content/metrics/index.md` list page (spec YAGNI)
- Alerting thresholds (spec YAGNI)
- pie chart alternative (spec explicitly rejects)
- Real historic backfill: PR ships forward-looking only; past `data/runs/*` won't auto-generate metrics
