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
    ax.invert_yaxis()
    ax.set_xlabel("items")

    prev = None
    for i, val in enumerate(values):
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
    _plot_waterfall(ax_funnel, data.get("funnel") or {})
    ax_funnel.set_title(f"today funnel ({data.get('date', '')})")
    fig.tight_layout()
    fig.savefig(out_path, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
