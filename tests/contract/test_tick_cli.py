import asyncio
import json
from datetime import datetime, timezone

import src.cli as cli_module
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


def test_run_tick_collect_does_not_call_storylink_llm(tmp_path, monkeypatch):
    """collect tick 只写 DB 列 + 推 review 卡, 都不读 story_id, link_stories 的 LLM
    调用应该只在 finalize tick 跑, collect tick 上跑纯属浪费(spec 2026-08-28 review)。"""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

    def _boom(*args, **kwargs):
        raise AssertionError("link_stories must not run on the collect tick")

    monkeypatch.setattr(cli_module, "link_stories", _boom)

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


def test_finalize_reuses_interpretations_written_by_collect(tmp_path, monkeypatch):
    """接线: collect 写的解读要能被 finalize 读到, 否则缓存只是摆设。
    finalize 用一个必然失败的 llm, 条目仍是 ok 就说明走的是缓存。"""
    from tests.fakes import FakeLLMProvider

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    ok = json.dumps(
        {
            "title": "中文标题",
            "body": "正文。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [],
            "relevant": True,
        }
    )
    kw = dict(
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
    )
    seen = {}
    real = cli_module.interpret

    def _spy(*a, **k):
        res = real(*a, **k)
        seen[k.get("cache") is not None] = res
        return res

    monkeypatch.setattr(cli_module, "interpret", _spy)
    run_tick(tick="collect", llm=FakeLLMProvider({}, default=ok), **kw)
    first = seen.pop(True)
    assert first.interpreted_count > 0
    run_tick(tick="finalize", llm=FailingLLMProvider(), **kw)
    assert seen[True].interpreted_count == first.interpreted_count
