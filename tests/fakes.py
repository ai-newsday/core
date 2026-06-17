from __future__ import annotations

from src.core.types import Genre, Publisher

# Default publisher per genre for synthetic test items (any valid combo is fine).
DEFAULT_PUBLISHER = {
    Genre.paper: Publisher.company,
    Genre.model: Publisher.company,
    Genre.announcement: Publisher.lab,
    Genre.writeup: Publisher.individual,
    Genre.news: Publisher.media,
}


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


class MisalignedEmbeddingProvider:
    """Violates the EmbeddingProvider 1:1 alignment contract by returning a list
    whose length differs from the input (spec §7 treats this as batch failure)."""

    def __init__(self, delta: int = -1):
        self._delta = delta

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        n = max(len(texts) + self._delta, 0)
        return [[1.0, 0.0] for _ in range(n)]


class FakeLLMProvider:
    """Returns canned JSON strings keyed by a substring of the prompt (e.g. the
    item's link). No key match -> `default`, or KeyError if default is None.
    Records every prompt in `.calls` for assertions (e.g. not-called on silent)."""

    def __init__(self, by_substring: dict[str, str], default: str | None = None):
        self._map = by_substring
        self._default = default
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        self.calls.append(prompt)
        for key, resp in self._map.items():
            if key in prompt:
                return resp
        if self._default is not None:
            return self._default
        raise KeyError("FakeLLMProvider: no canned response for prompt")


class FailingLLMProvider:
    """Simulates total LLM failure -> every item falls back to extractive."""

    def __init__(self):
        self.calls: list[str] = []

    def complete_json(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        self.calls.append(prompt)
        raise RuntimeError("llm provider unavailable")
