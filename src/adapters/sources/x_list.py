from __future__ import annotations

from pathlib import Path

from src.core.types import RawItem, RunContext, SourceSpec

# 对齐 src/pipeline/interpret.py:59 _SENT_ENDS
_SENT_ENDS = "。！？!?；;."


def _tweet_title(text: str, n: int = 140) -> str:
    """推文首行取标题, 超长按句末 → 词界 → 硬切+省略号 三档降级。

    n 默认 140 ≈ X 推文上限 280 的一半, 给后续 interpret 层 LLM 翻译/精炼留余地;
    interpret/review 层另有 title_max_chars=64 二次夹紧, 这里不参与最终长度。
    """
    first_line = text.split("\n", 1)[0].strip()
    if len(first_line) <= n:
        return first_line
    window = first_line[:n]
    cut = max((window.rfind(ch) for ch in _SENT_ENDS), default=-1)
    if cut >= 0:
        return window[: cut + 1]
    ws = window.rfind(" ")
    if ws > n // 2:
        return window[:ws] + "…"
    return first_line[: n - 1] + "…"


class XListAdapter:
    """Filesystem adapter: reads X (Twitter) list-timeline tweets from
    data/x/<date>.ndjson, routes by source.url == 'xlist:<list_id>'.

    PR-1: read-only, no LLM, no network. data_dir is constructor-injectable
    for tests; production singleton uses default ./data/x.
    """

    def __init__(self, data_dir: Path | str = "data/x") -> None:
        self._data_dir = Path(data_dir)

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        if not self._data_dir.is_dir():
            return []
        return []
