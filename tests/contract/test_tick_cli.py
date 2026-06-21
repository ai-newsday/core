import asyncio
import json
from datetime import datetime, timezone

from src.cli import run_tick
from src.state.db import Database
from tests.fakes import FailingLLMProvider, FakeEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_tick_collect_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    out = run_tick(
        tick="collect",
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    for k in ("run_id", "tick", "pushed", "date"):
        assert k in out


def test_run_tick_finalize_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    run_tick(
        tick="collect",
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    out = run_tick(
        tick="finalize",
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    for k in ("run_id", "tick", "item_count"):
        assert k in out
    json.dumps(out, ensure_ascii=False)


def test_run_tick_reads_seeded_quality_weights_without_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    db_path = str(tmp_path / "state.db")

    async def seed():
        db = Database(db_path)
        await db.init()
        await db.upsert_quality_weights({"hf-models": 1.5})

    asyncio.run(seed())

    out = run_tick(
        tick="collect",
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=db_path,
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    for k in ("run_id", "tick", "pushed", "date"):
        assert k in out
