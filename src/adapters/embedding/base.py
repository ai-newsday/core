from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float] | None]:
        """Return one vector per input text (aligned). None at an index means
        that single text failed; raise to signal a whole-batch/provider failure."""
        ...
