import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.decisions.worker import FakeDecisionStore, WorkerDecisionStore


def test_fake_decision_store_returns_dict_and_counts():
    store = FakeDecisionStore({"abc": "keep", "def": "drop"})
    out = asyncio.run(store.fetch())
    assert out == {"abc": "keep", "def": "drop"}
    assert store.fetch_count == 1


def test_worker_decision_store_parses_json_with_auth():
    async def go():
        with patch("src.adapters.decisions.worker.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            MockClient.return_value.__aenter__.return_value = client
            resp = MagicMock()
            resp.json.return_value = {"abc": "keep"}
            resp.raise_for_status = MagicMock()
            client.get.return_value = resp
            store = WorkerDecisionStore("https://w.example.com/", "sek", timeout_s=5)
            out = await store.fetch()
            assert out == {"abc": "keep"}
            args, kwargs = client.get.call_args
            assert args[0] == "https://w.example.com/decisions"
            assert kwargs["headers"]["Authorization"] == "Bearer sek"

    asyncio.run(go())
