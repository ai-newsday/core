"""storylink: 故事线合并候选识别(spec 2026-08-28)。两阶段:
1. 正则抓 entity token(型号名+版本号模式), 同一天内 token 重叠的两两配对成候选。
2. 候选对过一次轻量 LLM 确认(是非题, 不产出新文字), 确认为真才连边。
连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
LLM 调用失败/解析失败 -> fail-closed(不误合并)。"""

from __future__ import annotations

import json
import re
from datetime import timezone

from src.core.types import RunContext, ScoredItem, StoryLinkConfig
from src.observability.events import emit

_SEP_RE = re.compile(r"[-\s]+")


def extract_entity_tokens(text: str, pattern: str) -> set[str]:
    """正则抓"字母前缀+数字版本号"模式的 token, 规范化(大写 + 连字符/空格归一)去重。
    纯函数, 无副作用。空文本/无命中 -> 空集合。"""
    if not text:
        return set()
    hits = re.findall(pattern, text)
    return {_SEP_RE.sub("-", h.upper()) for h in hits}


def _item_text(item: ScoredItem) -> str:
    return f"{item.title_en} {item.raw_summary or ''}"


def _same_utc_day(a, b) -> bool:
    """判定两个 tz-aware datetime 是否落在同一 UTC 日历日(确定性比较, 不依赖 ctx.now)。"""
    return a.astimezone(timezone.utc).date() == b.astimezone(timezone.utc).date()


def find_candidate_pairs(items: list[ScoredItem], pattern: str) -> list[tuple[int, int]]:
    """同一 UTC 日历日 + entity token 集合有交集 -> 候选对(纯函数)。
    O(n²) 但 n 是发卡池量级(几十条), 可接受。返回 (i, j), i < j, 按原列表下标序。"""
    tokens = [extract_entity_tokens(_item_text(it), pattern) for it in items]
    pairs: list[tuple[int, int]] = []
    for i in range(len(items)):
        if not tokens[i]:
            continue
        for j in range(i + 1, len(items)):
            if not tokens[j]:
                continue
            if not _same_utc_day(items[i].published_at, items[j].published_at):
                continue
            if tokens[i] & tokens[j]:
                pairs.append((i, j))
    return pairs


def build_confirm_prompt(
    item_a: ScoredItem, item_b: ScoredItem, template: str, config: StoryLinkConfig
) -> str:
    """Render the pairwise confirm prompt. Summaries truncated to config.summary_max_chars
    (hard cut, no ellipsis needed -- this prompt only needs enough context to judge, not
    to quote back to the reader)."""
    n = config.summary_max_chars
    repl = {
        "{{title_a}}": item_a.title_en,
        "{{summary_a}}": (item_a.raw_summary or "")[:n],
        "{{title_b}}": item_b.title_en,
        "{{summary_b}}": (item_b.raw_summary or "")[:n],
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def _parse_same_story(raw: str) -> bool:
    data = json.loads(raw)
    if not isinstance(data, dict) or "same_story" not in data:
        raise ValueError("missing same_story key")
    if not isinstance(data["same_story"], bool):
        raise ValueError("same_story not bool")
    return data["same_story"]


def confirm_pair(
    item_a: ScoredItem, item_b: ScoredItem, template: str, llm, config: StoryLinkConfig
) -> bool:
    """一次 LLM 是非确认。任何失败(网络/解析) -> False(fail-closed, spec: 宁可错过
    一次合并机会, 不要把两个不相关条目错误拼一起给读者看)。"""
    try:
        prompt = build_confirm_prompt(item_a, item_b, template, config)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        return _parse_same_story(raw)
    except Exception:
        return False


class _UnionFind:
    """按下标合并; find() 路径压缩。纯粹的实现细节, 不导出。"""

    def __init__(self, n: int):
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra


def link_stories(
    items: list[ScoredItem], llm, config: StoryLinkConfig, ctx: RunContext
) -> list[ScoredItem]:
    """纯函数(除 llm 调用外无副作用)。两阶段:
    1. 正则抓 entity token, 同一天内 token 重叠的两两配对成候选(find_candidate_pairs)。
    2. 候选对过一次轻量 LLM 确认(confirm_pair), 确认为真才连边。
    连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
    返回顺序/数量与输入一致(spec: 合并只在 publish 层生效, 这里只打标)。"""
    emit(ctx.logger, "storylink_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "storylink_done", grouped_count=0)
        return items

    from src.core.prompts import load_prompt

    template = load_prompt(config.prompt_path)
    pairs = find_candidate_pairs(items, config.entity_token_pattern)
    uf = _UnionFind(len(items))
    confirmed = 0
    for i, j in pairs:
        if confirm_pair(items[i], items[j], template, llm, config):
            uf.union(i, j)
            confirmed += 1

    # group indices by root
    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)

    out = list(items)
    n = 0
    for idx_list in groups.values():
        if len(idx_list) < 2:
            continue
        n += 1
        sid = f"story-{ctx.now:%Y-%m-%d}-{n:03d}"
        for idx in idx_list:
            out[idx] = out[idx].model_copy(update={"story_id": sid})

    emit(ctx.logger, "storylink_done", grouped_count=n, confirmed_pairs=confirmed)
    return out
