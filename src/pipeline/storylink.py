"""storylink: 故事线合并候选识别(spec 2026-08-28)。两阶段:
1. 正则抓 entity token(型号名+版本号模式), 同一天内 token 重叠的两两配对成候选。
2. 候选对过一次轻量 LLM 确认(是非题, 不产出新文字), 确认为真才连边。
连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
LLM 调用失败/解析失败 -> fail-closed(不误合并)。"""

from __future__ import annotations

import re

_SEP_RE = re.compile(r"[-\s]+")


def extract_entity_tokens(text: str, pattern: str) -> set[str]:
    """正则抓"字母前缀+数字版本号"模式的 token, 规范化(大写 + 连字符/空格归一)去重。
    纯函数, 无副作用。空文本/无命中 -> 空集合。"""
    if not text:
        return set()
    hits = re.findall(pattern, text)
    return {_SEP_RE.sub("-", h.upper()) for h in hits}
