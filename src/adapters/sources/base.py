from typing import Protocol

from src.core.types import RawItem, RunContext, SourceSpec


class SourceAdapter(Protocol):
    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]: ...
