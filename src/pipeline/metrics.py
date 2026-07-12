"""Pure metrics functions: read per-run jsonl → funnel counts + derived rates.

No IO other than reading the passed run_dir. No side effects.
Ships zero-safe: missing files → 0 counts; division by zero → 0.0 rates.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
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
        "interpreted_fallback": _count_matching(
            interpreted, "interpretation_status", "extractive_fallback"
        ),
        "review_eligible": _count_lines(reviewed),
        "posted": _count_matching(reviewed, "review_action", "keep"),
    }


def _safe_ratio(num: float, denom: float) -> float:
    return num / denom if denom else 0.0


def compute_rates(funnel: dict[str, int]) -> dict[str, float]:
    interpreted_total = funnel["interpreted_ok"] + funnel["interpreted_fallback"]
    return {
        "fallback_rate": _safe_ratio(funnel["interpreted_fallback"], interpreted_total),
        "dedup_reduction": 1.0 - _safe_ratio(funnel["after_dedup"], funnel["candidates"])
        if funnel["candidates"]
        else 0.0,
        "quota_reduction": 1.0 - _safe_ratio(funnel["after_score_quota"], funnel["after_dedup"])
        if funnel["after_dedup"]
        else 0.0,
        "interpret_fail_rate": _safe_ratio(funnel["interpreted_fallback"], interpreted_total),
        "keep_rate": _safe_ratio(funnel["posted"], funnel["review_eligible"]),
    }


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
    collected = Counter(
        row.get("genre", "unknown") for row in _iter_rows(run_dir / "01_collected.jsonl")
    )
    posted = Counter(
        row.get("genre", "unknown")
        for row in _iter_rows(run_dir / "05_reviewed.jsonl")
        if row.get("review_action") == "keep"
    )
    out: dict[str, dict[str, int | float | None]] = {}
    for genre, cand in collected.items():
        p = posted.get(genre, 0)
        out[genre] = {
            "candidates": cand,
            "posted": p,
            # 0 candidates → None (undefined ratio), matches load_trend_7d.eligible_rate semantics
            "noise_ratio": (1.0 - (p / cand)) if cand else None,
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
        rows.append(
            {
                "name": name,
                "yield": y,
                "kept": k,
                # 0 yield → None (undefined ratio), matches load_trend_7d.eligible_rate semantics
                "noise_ratio": (1.0 - (k / y)) if y else None,
            }
        )
    rows.sort(key=lambda r: r["yield"], reverse=True)
    return rows[:10]


def load_fallback_titles(run_dir: Path, limit: int = 3) -> list[str]:
    """Titles from interpret rows where interpretation_status == 'extractive_fallback'."""
    if limit <= 0:
        return []
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
