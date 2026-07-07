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
