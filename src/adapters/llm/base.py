from typing import Protocol


class LLMProvider(Protocol):
    def complete_json(self, prompt: str, *, temperature: float,
                      max_tokens: int) -> str:
        """Return the model's raw text completion (expected to be JSON).
        Raise to signal a provider/network failure (caller falls back)."""
        ...
