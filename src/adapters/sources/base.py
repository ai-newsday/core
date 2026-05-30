from typing import Protocol
from src.core.types import SourceSpec, RunContext, RawItem


class SourceAdapter(Protocol):
    async def fetch(self, source: SourceSpec, ctx: RunContext,
                    timeout_s: int) -> list[RawItem]:
        ...
