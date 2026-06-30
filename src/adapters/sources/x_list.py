from __future__ import annotations

from pathlib import Path

from src.core.types import RawItem, RunContext, SourceSpec


class XListAdapter:
    """Filesystem adapter: reads X (Twitter) list-timeline tweets from
    data/x/<date>.ndjson, routes by source.url == 'xlist:<list_id>'.

    PR-1: read-only, no LLM, no network. data_dir is constructor-injectable
    for tests; production singleton uses default ./data/x.
    """

    def __init__(self, data_dir: Path | str = "data/x") -> None:
        self._data_dir = Path(data_dir)

    async def fetch(
        self, source: SourceSpec, ctx: RunContext, timeout_s: int
    ) -> list[RawItem]:
        if not self._data_dir.is_dir():
            return []
        return []
