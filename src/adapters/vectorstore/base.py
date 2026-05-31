from typing import Protocol, Any


class VectorStore(Protocol):
    def upsert(self, points: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        """Persist (id, vector, payload) triples. Real impl = Qdrant (deferred)."""
        ...
