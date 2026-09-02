from __future__ import annotations

import re
from urllib.parse import urlparse

from src.core.types import (
    CategorySection,
    DailyReport,
    Evidence,
    Overview,
    PublishConfig,
    PublishResult,
    ReviewedItem,
    ReviewResult,
    RunContext,
)
from src.observability.events import emit
from src.pipeline.interpret import _trim_to_sentence
from src.pipeline.score import apply_adapter_quota, apply_quota


def select_must_read(items: list[ReviewedItem], config: PublishConfig) -> list[ReviewedItem]:
    """合格(eligible)条目里按上游序取前 must_read_count 条。(保留兼容，渲染层已不使用)"""
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
    """genre 分布计数(按 genre_labels 键序) + 高频关键词(聚合 tags 去 # 取 Top N)。
    (保留兼容，渲染层已不使用)"""
    from collections import Counter

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


def _pre_content_certainty_penalty_score(item: ReviewedItem) -> int:
    """min_display_score 地板检查用: 内容确定性扣分只该让条目在 apply_quota()
    重排时输给分数更高的同类, 不该把一条人工已 keep 的条目直接从地板下面挤没
    ——那不是配额位的竞争结果, 是这次扣分单方面造成的(用户已确认: 人工 keep
    的条目应该保留, 扣分只影响排序, 不影响这层"保护 keep 的低分首发"的地板)。
    """
    penalty = item.score_breakdown.get("内容确定性", 0.0)
    return item.score - round(penalty) if penalty < 0 else item.score


def _support_display_name(link: str) -> str:
    """支持平台的展示名: 用链接域名, 不用 item.source(内部配置 slug)——今天刚修过
    source 泄漏喂给 LLM 当事实这个坑(#102), 渲染层不能重新踩一遍。跟
    telegram_polling.py::_make_card_message 的 link_domain 是同一约定。
    # ponytail: 取末两段够用(together.ai/ollama.com); example.co.uk 这类多段公共后缀会退化成 co.uk, 等做了平台名映射表再换
    """
    host = urlparse(link).netloc or link
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) > 2 else host


_DEFAULT_SUPPORT_TEMPLATE = "\n\n目前已知 {names} 等平台跟进支持。"


def merge_story_groups(
    items: list[ReviewedItem],
    max_support: int = 3,
    support_template: str = _DEFAULT_SUPPORT_TEMPLATE,
) -> list[ReviewedItem]:
    """按 story_id 分组; 组内按 published_at 升序, 最早=原始发布(留作 primary);
    其余按 -score 排序取前 max_support 个当"已支持平台", 拼进 primary.body 末尾一句,
    并把它们的 link 登记进 primary.evidence(复用 _ref() 的既有"anchor -> 参考表"渲染
    路径, 渲染层不用改)。组内其余条目(含超出 max_support 的部分)从结果中剔除,
    不再单独占位。story_id 为 None 的条目原样透传。"""
    groups: dict[str, list[ReviewedItem]] = {}
    passthrough: list[ReviewedItem] = []
    for it in items:
        if it.story_id is None:
            passthrough.append(it)
        else:
            groups.setdefault(it.story_id, []).append(it)

    out: list[ReviewedItem] = list(passthrough)
    for group in groups.values():
        # published_at 相同时(同秒/同批常见), 单靠时间戳排序不确定——补 link 当第二键,
        # 保证同一组数据无论输入顺序如何, primary 都稳定选中同一条(重跑不该换脸)。
        ordered = sorted(group, key=lambda it: (it.published_at, it.link))
        primary, rest = ordered[0], ordered[1:]
        support = sorted(rest, key=lambda it: -it.score)[:max_support]
        if not support:
            out.append(primary)
            continue
        existing_anchors = {e.anchor for e in primary.evidence}
        names = [_support_display_name(it.link) for it in support]
        suffix = support_template.format(names="、".join(names))
        new_evidence = list(primary.evidence)
        for name, it in zip(names, support):
            if it.link in existing_anchors:
                continue
            existing_anchors.add(it.link)
            new_evidence.append(Evidence(claim=name, anchor=it.link))
        out.append(
            primary.model_copy(update={"body": primary.body + suffix, "evidence": new_evidence})
        )
    return out


def build_report(
    review_result: ReviewResult, date_label: str, config: PublishConfig
) -> DailyReport:
    """组装内容模型: score floor 过滤 + 分类分组 + 元信息。"""
    items = [
        it
        for it in review_result.reviewed_items
        if _pre_content_certainty_penalty_score(it) >= config.min_display_score
        and it.relevant
        # 2026-09-02 实测: agnes 推理预算烧穿时, 解读失败 + 原文摘要本身也是空的
        # 两个条件叠加, 会产出一条 body 完全空白的卡片——比英文回退条目更差,
        # 读者看到的是标题下面什么都没有。宁可少发一条, 不发一张空卡片。
        and it.body.strip()
    ]
    # 采集渠道封顶(spec §5): 先砍 GitHub 超额, 让 genre 配额的剩余名额优先给非 GitHub 条目
    items, _ = apply_adapter_quota(items, config.adapter_quota)
    # 故事线合并(spec 2026-08-28): 先把同故事的条目收成一条再占配额, 不然故事组
    # 可能因为占了多个配额位反而把其它公司当天的公告挤掉 —— 合并要先于配额生效
    # 才能真正省位置。
    items = merge_story_groups(
        items, config.story_merge_max_support, config.story_merge_support_template
    )
    # per-genre 配额 + total_limit: 人 keep 之后对 kept 集合施加(组成控制, 复用 score 纯函数)
    items, _ = apply_quota(items, config.quota, config.total_limit)
    return DailyReport(
        date_label=date_label,
        daily_take=review_result.daily_take,
        wechat_title=review_result.wechat_title,
        must_read=[],
        categories=group_by_category(items, config),
        overview=Overview(genre_distribution={}, keywords=[]),
        is_pending=review_result.is_pending,
        item_count=len(items),
        explore_count=sum(1 for it in items if it.is_explore),
    )


def _escape_math_delimiters(text: str) -> str:
    """转义正文里的 `$`。成对的 `$` 会被 Markdown 当成行内数学定界符, 把中间的文字
    吃掉、把周围拆成零散的强调标记——2026-09-01 实测 `最佳模型成本低于 $6.9K，性能
    逼近 Qwen2.5-1.5B` 渲染成了 `***，*** **性** **能** **逼** **近**`。

    prompt 已要求金额写「6900 美元」而不是 `$6900`(#131), 这里是兜底: 硬约束不能
    只靠 prompt(同期教训: prompt 写「必须 ≤120 字」实测产出 145 字)。CommonMark 的
    `\\$` 转义渲染成字面 `$`, 且不会触发数学模式。"""
    return text.replace("$", "\\$")


def _render_items(report: DailyReport) -> tuple[list[str], list[tuple[str, str]]]:
    """条目顺读渲染(2026-08-05 改版)。

    去掉 `## {genre}` 大分类标题: 分类对读者没有导航价值, 同一件事被哪个源先抓到就
    归哪一类(Mistral 的模型发布走博客 RSS 就成了"官方"), 标题反而误导。条目**顺序**
    仍按 genre_labels 键序, 只是不再显示分组标题。

    链接全部收进文末"参考链接"章节, 正文只留 `[n]` 编号 —— 正文里不再有裸 URL。
    返回 (正文行, 参考表), 参考表 = [(展示名, url)] 按首次出现顺序。
    """
    lines: list[str] = []
    refs: list[tuple[str, str]] = []

    def _ref(label: str, url: str) -> int:
        """登记一条参考链接, 返回它的编号(1 起)。同一 URL 复用同一个号。"""
        for i, (_, existing) in enumerate(refs, start=1):
            if existing == url:
                return i
        refs.append((label, url))
        return len(refs)

    for cat in report.categories:
        for it in cat.items:
            # 2026-09-02 用户改用二级标题(##): 与文末"## 参考链接"同级, 便于
            # doocs/md 等公众号排版工具按标题层级套用统一样式。
            lines.append(f"## {it.title}")
            lines.append("")
            if it.image_url:
                lines.append(f"![]({it.image_url})")
                lines.append("")
            lines.append(_escape_math_delimiters(it.body))
            lines.append("")
            if it.tags:
                lines.append(" ".join(it.tags))
            # 来源恒占一个编号, 用条目标题当参考表显示名(不是源名, 方便读者认出是哪条);
            # 依据锚点等于来源链接时由 _ref 去重, 不会多编号
            # (延续 2026-07-25 的"同一 URL 不重复罗列"规则)。
            nums = [_ref(it.title, it.link)]
            nums += [_ref(e.claim, e.anchor) for e in it.evidence if e.anchor != it.link]
            # 不再输出分数: 实测一期九条评分全挤在 94-100, 对读者零区分度,
            # 还把内部打分暴露出去(用户 2026-08-31 决定去掉)。
            marks = "".join(f"[{n}]" for n in dict.fromkeys(nums))
            lines.append(marks)
            lines.append("")
    return lines, refs


def _render_references(refs: list[tuple[str, str]]) -> list[str]:
    """文末参考链接章节。空表不产章节。"""
    if not refs:
        return []
    lines = ["## 参考链接", ""]
    lines += [f"{i}. [{label}]({url})" for i, (label, url) in enumerate(refs, start=1)]
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
    # 截到句末而不是硬切: 硬切 140 曾把 "DeepSeek" 切成 "De" 出现在站点 meta /
    # 社交预览里(2026-09-01 实测)。daily_take 现在本身已被 enforce_digest 卡在
    # 120 字内, 这层只是兜底, 但兜底也不该切在词中间。
    summary = _trim_to_sentence(report.daily_take or "", 140)
    lines = [
        "---",
        f"title: {_yaml_quote(report.wechat_title or 'AI Daily · ' + report.date_label)}",
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
    lines: list[str] = [f"# {report.wechat_title or 'AI Daily · ' + report.date_label}", ""]
    if report.is_pending:
        lines.append(f"> {config.pending_watermark}")
        lines.append("")
    if report.daily_take:
        # 不再硬编码 `**今日看点**：` 前缀: 摘要自身的固定格式已经以 `今日亮点：`
        # 开头(spec 2026-08-31), 两者叠加会渲染成 `今日看点：今日亮点：…`
        # (2026-09-01 线上实测)。摘要自带标签, 渲染层只负责引用块。
        lines.append(f"> {report.daily_take}")
        lines.append("")
    body_lines, refs = _render_items(report)
    lines += body_lines
    lines += _render_references(refs)
    lines.append("---")
    lines.append("RSS · 历史归档 · 主站 ｜ AI News Daily")
    return "\n".join(lines)


def render(report: DailyReport, config: PublishConfig, ctx: RunContext) -> PublishResult:
    """跟 publish() 共享的"渲染非空报告"步骤, 拆出来是为了让调用方能在
    build_report() 之后、真正渲染 Markdown 之前插入修改——比如 #139 用配额筛选后
    的最终条目重新生成标题/摘要、逐条配图——而不必让 publish() 重新跑一遍
    build_report() 把这些修改冲掉。只用于已知非空(item 数量已由调用方保证)的
    报告; 空报告的静默短路仍由 publish() 处理。"""
    emit(
        ctx.logger,
        "report_built",
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
        is_pending=report.is_pending,
        silent=False,
    )
    return PublishResult(
        report=report, markdown=markdown, is_pending=report.is_pending, is_silent=False
    )


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
            is_pending=report.is_pending,
            silent=True,
        )
        return PublishResult(
            report=report, markdown="", is_pending=report.is_pending, is_silent=True
        )
    return render(report, config, ctx)
