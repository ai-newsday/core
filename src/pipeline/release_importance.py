"""release_importance: LLM 判定 github_releases 条目的实质重要性 (spec 2026-07-22)。
只处理 adapter == "github_releases" 的条目; 其余原样透传。空 body 短路判 tier 0
(不调 LLM); 否则 LLM 判 4 个独立布尔维度, tier() 纯函数映射到最终档位。
tier <= hard_filter_max_tier 的条目从返回列表剔除; tier >= 2 的条目写
signals["release_tier_score"] 参与打分 (复用 popularity_weights 机制)。
LLM 调用失败/解析失败 -> fail-open, 视为 tier=2 (放行 + 中性打分), 不硬删。"""

from __future__ import annotations


def tier(scale: bool, refactor: bool, new_concept: bool, bugfix_only: bool) -> int:
    """4 个独立布尔维度 -> 最终档位 (纯函数)。
    refactor/new_concept 命中 -> 3 (有规模) 或 2 (无规模);
    否则 scale 且非纯 bugfix -> 2; 其余(含空组合、纯 bugfix) -> 1。"""
    if refactor or new_concept:
        return 3 if scale else 2
    if scale and not bugfix_only:
        return 2
    return 1
