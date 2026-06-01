from __future__ import annotations
from collections import Counter
from src.core.types import (ReviewedItem, PublishConfig, Overview,
                            CategorySection, DailyReport, PublishResult,
                            ReviewResult, RunContext)
from src.observability.events import emit


def select_must_read(items: list[ReviewedItem],
                     config: PublishConfig) -> list[ReviewedItem]:
    """合格(eligible)条目里按上游序取前 must_read_count 条。"""
    eligible = [it for it in items if it.eligible_for_must_read]
    return eligible[:config.must_read_count]


def group_by_category(items: list[ReviewedItem],
                      config: PublishConfig) -> list[CategorySection]:
    """按 source_type 分组; 组间按 type_labels 键序(不在表里的排末尾);
    组内保上游序; 空类目不产 section。"""
    order = list(config.type_labels)
    seen: list[str] = []
    buckets: dict[str, list[ReviewedItem]] = {}
    for it in items:
        st = it.source_type.value
        if st not in buckets:
            buckets[st] = []
            seen.append(st)
        buckets[st].append(it)

    def rank(st: str) -> tuple[int, int]:
        return (order.index(st), 0) if st in order else (len(order), seen.index(st))

    out: list[CategorySection] = []
    for st in sorted(seen, key=rank):
        out.append(CategorySection(
            source_type=st, label=config.type_labels.get(st, st),
            items=buckets[st]))
    return out


def build_overview(items: list[ReviewedItem],
                   config: PublishConfig) -> Overview:
    """类型分布计数(按 type_labels 键序) + 高频关键词(聚合 tags 去 # 取 Top N)。"""
    order = list(config.type_labels)
    counts = Counter(it.source_type.value for it in items)
    dist = {st: counts[st] for st in order if counts.get(st)}
    for st in counts:                       # 不在表里的类型补在后面
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
    return Overview(type_distribution=dist,
                    keywords=ranked[:config.top_keywords])
