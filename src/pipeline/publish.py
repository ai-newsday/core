from __future__ import annotations

import re
from collections import Counter

from src.core.types import (
    CategorySection,
    DailyReport,
    Overview,
    PublishConfig,
    PublishResult,
    ReviewedItem,
    ReviewResult,
    RunContext,
)
from src.observability.events import emit


def select_must_read(items: list[ReviewedItem], config: PublishConfig) -> list[ReviewedItem]:
    """合格(eligible)条目里按上游序取前 must_read_count 条。"""
    eligible = [it for it in items if it.eligible_for_must_read]
    return eligible[: config.must_read_count]


def group_by_category(items: list[ReviewedItem], config: PublishConfig) -> list[CategorySection]:
    """按 genre 分组; 组间按 genre_labels 键序(不在表里的排末尾);
    组内保上游序; 空类目不产 section。"""
    order = list(config.genre_labels)
    seen: list[str] = []
    buckets: dict[str, list[ReviewedItem]] = {}
    for it in items:
        st = it.genre.value
        if st not in buckets:
            buckets[st] = []
            seen.append(st)
        buckets[st].append(it)

    def rank(st: str) -> tuple[int, int]:
        return (order.index(st), 0) if st in order else (len(order), seen.index(st))

    out: list[CategorySection] = []
    for st in sorted(seen, key=rank):
        out.append(
            CategorySection(genre=st, label=config.genre_labels.get(st, st), items=buckets[st])
        )
    return out


def build_overview(items: list[ReviewedItem], config: PublishConfig) -> Overview:
    """genre 分布计数(按 genre_labels 键序) + 高频关键词(聚合 tags 去 # 取 Top N)。"""
    order = list(config.genre_labels)
    counts = Counter(it.genre.value for it in items)
    dist = {st: counts[st] for st in order if counts.get(st)}
    for st in counts:  # 不在表里的类型补在后面
        if st not in dist:
            dist[st] = counts[st]

    freq: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    seq = 0
    for it in items:
        for tag in it.tags:
            kw = tag.lstrip("#")
            if not kw:
                continue
            if kw not in first_seen:
                first_seen[kw] = seq
                seq += 1
            freq[kw] += 1
    ranked = sorted(freq, key=lambda k: (-freq[k], first_seen[k]))
    return Overview(genre_distribution=dist, keywords=ranked[: config.top_keywords])


def build_report(
    review_result: ReviewResult, date_label: str, config: PublishConfig
) -> DailyReport:
    """组装内容模型: 必读 + 分类速览 + 数据概览 + 元信息。"""
    items = review_result.reviewed_items
    return DailyReport(
        date_label=date_label,
        daily_take=review_result.daily_take,
        must_read=select_must_read(items, config),
        categories=group_by_category(items, config),
        overview=build_overview(items, config),
        is_pending=review_result.is_pending,
        item_count=len(items),
        explore_count=sum(1 for it in items if it.is_explore),
    )


def _render_must_read(report: DailyReport, label_of: dict[str, str]) -> list[str]:
    lines = ["## 🏆 今日必读", ""]
    for i, it in enumerate(report.must_read, 1):
        label = label_of.get(it.genre.value, it.genre.value)
        lines.append(f"### {i}. [{label}] {it.title}（{it.title_en}）")
        lines.append(f"{it.body}")
        lines.append(f"- **评分**：{it.score} ｜ **来源**：[{it.source}]({it.link})")
        if it.evidence:
            ev = "；".join(f"[{e.claim}]({e.anchor})" for e in it.evidence)
            lines.append(f"- **依据**：{ev}")
        lines.append("")
    return lines


def _render_categories(report: DailyReport) -> list[str]:
    lines = ["## 📚 分类速览", ""]
    for cat in report.categories:
        lines.append(f"**{cat.label}**")
        for it in cat.items:
            mark = " 🧭探索" if it.is_explore else ""
            lines.append(
                f"- `[{it.score}]`{mark} {it.title} — {it.body} ｜ [{it.source}]({it.link})"
            )
        lines.append("")
    return lines


def _render_overview(report: DailyReport, label_of: dict[str, str]) -> list[str]:
    lines = ["## 📊 数据概览"]
    dist = "｜".join(
        f"{label_of.get(st, st)} {n}" for st, n in report.overview.genre_distribution.items()
    )
    lines.append(f"- 分类分布：{dist}")
    if report.overview.keywords:
        lines.append("- 高频关键词：" + "、".join(report.overview.keywords))
    lines.append("")
    return lines


def _yaml_quote(s: str) -> str:
    """双引号包裹并转义内嵌双引号 + 换行(够用的最小 YAML 标量转义)。
    换行必须转义: daily_take 是 LLM 输出, 裸换行会把单行 front matter 标量撑断, Hugo 解析失败。"""
    return (
        '"'
        + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        + '"'
    )


def render_front_matter(report: DailyReport, config: PublishConfig, draft: bool) -> str:
    """Hugo front matter(确定性, 无 now)。date 取 date_label 的 YYYY-MM-DD 前缀,
    固定东八区 08:00。tags = categories 的 label(已去重 + genre_labels 序)。"""
    m = re.match(r"\d{4}-\d{2}-\d{2}", report.date_label)
    iso_date = m.group(0) if m else report.date_label
    tags = ", ".join(_yaml_quote(c.label) for c in report.categories)
    summary = (report.daily_take or "")[:140]
    lines = [
        "---",
        f"title: {_yaml_quote('AI Daily · ' + report.date_label)}",
        f"date: {iso_date}T08:00:00+08:00",
        f"draft: {'true' if draft else 'false'}",
        f"tags: [{tags}]",
        f"summary: {_yaml_quote(summary)}",
        "---",
    ]
    return "\n".join(lines)


def flip_draft(text: str) -> str:
    """把 front matter 里的 `draft: true` 行替换为 `draft: false`(幂等)。
    只改第一处 (count=1): front matter 在文件最前, 故首个 draft 键即 front matter,
    正文里出现的 `draft: true`(如代码块/引用) 不受影响。无匹配则原样返回。"""
    return re.sub(r"(?m)^(\s*draft:\s*)true\s*$", r"\1false", text, count=1)


def render_markdown(report: DailyReport, config: PublishConfig) -> str:
    """把 DailyReport 渲染成 Markdown(确定性, 无 now)。"""
    label_of = config.genre_labels
    lines: list[str] = [f"# AI Daily · {report.date_label}", ""]
    if report.is_pending:
        lines.append(f"> {config.pending_watermark}")
        lines.append("")
    if report.daily_take:
        lines.append(f"> **今日看点**：{report.daily_take}")
        lines.append("")
    if report.must_read:
        lines += _render_must_read(report, label_of)
    if report.categories:
        lines += _render_categories(report)
    lines += _render_overview(report, label_of)
    lines.append("---")
    lines.append("📬 RSS ｜ 🗂 历史归档 ｜ 🏠 主站")
    return "\n".join(lines)


def publish(
    review_result: ReviewResult, date_label: str, config: PublishConfig, ctx: RunContext
) -> PublishResult:
    """编排: 空→静默; 否则组装内容模型并渲染 Markdown。无网络/LLM/渠道副作用。"""
    items = review_result.reviewed_items
    emit(ctx.logger, "publish_start", run_id=ctx.run_id, input_count=len(items))
    report = build_report(review_result, date_label, config)
    if not items:
        emit(
            ctx.logger,
            "publish_done",
            item_count=0,
            must_read_count=0,
            is_pending=report.is_pending,
            silent=True,
        )
        return PublishResult(
            report=report, markdown="", is_pending=report.is_pending, is_silent=True
        )
    emit(
        ctx.logger,
        "report_built",
        must_read_count=len(report.must_read),
        category_count=len(report.categories),
        item_count=report.item_count,
        is_pending=report.is_pending,
    )
    markdown = (
        render_front_matter(report, config, draft=True) + "\n" + render_markdown(report, config)
    )
    emit(
        ctx.logger,
        "publish_done",
        item_count=report.item_count,
        must_read_count=len(report.must_read),
        is_pending=report.is_pending,
        silent=False,
    )
    return PublishResult(
        report=report, markdown=markdown, is_pending=report.is_pending, is_silent=False
    )
