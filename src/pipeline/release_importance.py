"""release_importance: LLM 判定 github_releases 条目的实质重要性 (spec 2026-07-22)。
只处理 adapter == "github_releases" 的条目; 其余原样透传。空 body 短路判 tier 0
(不调 LLM); 否则 LLM 判 4 个独立布尔维度, tier() 纯函数映射到最终档位。
tier <= hard_filter_max_tier 的条目从返回列表剔除; tier >= 2 的条目写
signals["release_tier_score"] 参与打分 (复用 popularity_weights 机制)。
LLM 调用失败/解析失败 -> fail-open, 视为 tier=2 (放行 + 中性打分), 不硬删。"""

from __future__ import annotations

import json
import re

from src.core.prompts import load_prompt
from src.core.types import RawItem, ReleaseImportanceConfig, RunContext
from src.observability.events import emit


def tier(scale: bool, refactor: bool, new_concept: bool, bugfix_only: bool) -> int:
    """4 个独立布尔维度 -> 最终档位 (纯函数)。
    refactor/new_concept 命中 -> 3 (有规模) 或 2 (无规模);
    否则 scale 且非纯 bugfix -> 2; 其余(含空组合、纯 bugfix) -> 1。"""
    if refactor or new_concept:
        return 3 if scale else 2
    if scale and not bugfix_only:
        return 2
    return 1


_FULL_CHANGELOG_RE = re.compile(r"\*\*Full Changelog\*\*:\s*\S+")
_DIM_KEYS = ("scale", "refactor", "new_concept", "bugfix_only")


def _effective_body_len(raw_summary: str | None) -> int:
    """去掉 '**Full Changelog**: <url>' 比较链接后剩余的正文字符数(去首尾空白)。"""
    text = _FULL_CHANGELOG_RE.sub("", raw_summary or "")
    return len(text.strip())


def _parse_dims(raw: str) -> tuple[bool, bool, bool, bool]:
    """解析 LLM 输出的 4 个布尔维度。缺字段/非法 JSON -> 抛 ValueError (调用方 fail-open)。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    if not all(k in data for k in _DIM_KEYS):
        raise ValueError(f"missing dimension keys, got {list(data.keys())}")
    return tuple(bool(data[k]) for k in _DIM_KEYS)  # type: ignore[return-value]


def build_prompt(item: RawItem, template: str) -> str:
    body = (item.raw_summary or "")[:3000]
    return template.replace("{{title}}", item.title_en).replace("{{body}}", body)


def judge_release_importance(
    items: list[RawItem], llm, config: ReleaseImportanceConfig, ctx: RunContext
) -> list[RawItem]:
    """对 adapter == "github_releases" 的条目判定重要性; 其余原样透传。
    返回硬过滤后的列表(tier <= config.hard_filter_max_tier 的条目被剔除)。"""
    emit(ctx.logger, "release_importance_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "release_importance_done", judged=0, filtered=0)
        return items

    template = load_prompt(config.prompt_path)
    out: list[RawItem] = []
    filtered = 0
    for item in items:
        if item.adapter != "github_releases":
            out.append(item)
            continue

        if _effective_body_len(item.raw_summary) < config.empty_body_min_chars:
            t = 0
        else:
            try:
                prompt = build_prompt(item, template)
                raw = llm.complete_json(
                    prompt, temperature=config.temperature, max_tokens=config.max_tokens
                )
                scale, refactor, new_concept, bugfix_only = _parse_dims(raw)
                t = tier(scale, refactor, new_concept, bugfix_only)
            except Exception as e:
                emit(
                    ctx.logger,
                    "release_importance_error",
                    link=item.link,
                    error_type=type(e).__name__,
                    error=str(e)[:200],
                )
                t = 2  # fail-open: 放行 + 中性打分, 不硬删

        if t <= config.hard_filter_max_tier:
            filtered += 1
            continue
        if t >= 2:
            item.signals["release_tier_score"] = config.tier_score.get(t, 0.0)
        out.append(item)

    emit(ctx.logger, "release_importance_done", judged=len(items) - filtered, filtered=filtered)
    return out
