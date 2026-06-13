from __future__ import annotations

from typing import Any


class InMemoryVectorStore:
    """This-circle stand-in for Qdrant; also acts as the dry-run no-op store
    (nothing persists beyond the process)."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}

    def upsert(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        for pid, vector, payload in points:
            self.points[pid] = (vector, payload)
