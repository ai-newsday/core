from __future__ import annotations
import httpx

_BASE_URL = "https://api-inference.modelscope.cn/v1/embeddings"


class ModelScopeEmbedder:
    """OpenAI-compatible embeddings via ModelScope API-Inference."""

    def __init__(self, api_key: str, model: str, batch_size: int = 32,
                 timeout_s: int = 30):
        self._api_key = api_key
        self._model = model
        self._batch = max(1, batch_size)
        self._timeout = timeout_s

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        out: list[list[float] | None] = []
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            for i in range(0, len(texts), self._batch):
                chunk = texts[i:i + self._batch]
                r = client.post(_BASE_URL, headers=headers,
                                json={"model": self._model, "input": chunk})
                r.raise_for_status()
                data = r.json()["data"]
                out.extend(d["embedding"] for d in data)
        return out
