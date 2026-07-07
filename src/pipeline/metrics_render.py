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


def _largest_loss_stage(funnel: dict) -> str:
    """Return e.g. 'quota (掉 64.7%)' for the funnel stage with biggest drop."""
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
    for genre, stats in sorted(
        per_genre.items(), key=lambda kv: kv[1].get("candidates", 0), reverse=True
    ):
        noise = stats.get("noise_ratio") or 0.0
        lines.append(
            f"| {genre} | {stats.get('candidates', 0)} | {stats.get('posted', 0)} | {100.0 * noise:.1f}% |"
        )

    lines += ["", "## fallback 样本 (翻译失效的 title)", ""]
    for t in samples.get("fallback_titles") or []:
        lines.append(f"- {t}")

    lines += ["", f"[原始 JSON](./{date_str}.json)", ""]
    return "\n".join(lines)


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
        noise_pct = 100.0 * (top.get("noise_ratio") or 0.0)
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
