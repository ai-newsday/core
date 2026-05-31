from __future__ import annotations


class FakeEmbeddingProvider:
    """Returns frozen vectors keyed by exact input text. Missing key -> KeyError.
    A mapped value of None marks that single text as failed (spec §7 single-fail)."""

    def __init__(self, vectors_by_text: dict[str, list[float] | None]):
        self._map = vectors_by_text

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        return [self._map[t] for t in texts]


class FailingEmbeddingProvider:
    """Simulates total provider failure (spec §7 degrade-to-singletons)."""

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        raise RuntimeError("embedding provider unavailable")
