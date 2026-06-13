import json
from datetime import datetime, timezone

from src.cli import run_tick
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
    for k in ("run_id", "tick", "item_count", "must_read_count"):
        assert k in out
    json.dumps(out, ensure_ascii=False)
