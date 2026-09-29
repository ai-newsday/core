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


def test_finalize_reinterprets_kept_snapshots_without_collecting(tmp_path, monkeypatch):
    """A1 接线: 走 run_tick 真实入口。collect 写快照 → finalize 不调 collect、
    只对 keep 条目绕过缓存(cache=None)重新解读, keep 那条进报告。"""
    from src.adapters.decisions.worker import FakeDecisionStore
    from src.core.types import CollectionResult, Genre, Publisher, RawItem
    from src.notifiers import FakeNotifier
    from src.pipeline.tick import _item_id
    from tests.fakes import FakeLLMProvider

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("DECISIONS_API_SECRET", "x")
    ok = json.dumps(
        {
            "title": "中文标题",
            "body": "正文。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "c", "anchor": "https://openai.com/news/0"}],
            "relevant": True,
        }
    )
    kw = dict(
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        db_path=str(tmp_path / "state.db"),
        embedder=FakeEmbeddingProvider({}),
    )
    raw = [
        RawItem(
            title_en=f"OpenAI ships thing {i}",
            link=f"https://openai.com/news/{i}",
            source="openai",
            genre=Genre.announcement,
            publisher=Publisher.lab,
            published_at=NOW,
            raw_summary="A summary.",
            adapter="rss",
        )
        for i in range(3)
    ]

    async def _fixed_collect(cfg, ctx):
        return CollectionResult(items=list(raw), source_reports=[], is_silent=False)

    monkeypatch.setattr(cli_module, "collect", _fixed_collect)
    run_tick(tick="collect", llm=FakeLLMProvider({}, default=ok), **kw)

    def _boom(*a, **k):
        raise AssertionError("finalize must not collect")

    class _NoImages:
        async def fetch_html(self, url):
            return None

        async def check_image(self, url):
            return False

    keep_id = _item_id(raw[0])
    monkeypatch.setattr(cli_module, "collect", _boom)
    monkeypatch.setattr(
        cli_module, "WorkerDecisionStore", lambda *a: FakeDecisionStore({keep_id: "keep"})
    )
    monkeypatch.setattr(cli_module, "WebsiteNotifier", lambda cfg: FakeNotifier())
    monkeypatch.setattr(cli_module, "ItemImageClient", lambda **k: _NoImages())
    seen = []
    real = cli_module.interpret

    def _spy(items, *a, **k):
        seen.append((len(items), k.get("cache")))
        return real(items, *a, **k)

    monkeypatch.setattr(cli_module, "interpret", _spy)
    out = run_tick(tick="finalize", llm=FakeLLMProvider({}, default=ok), **kw)
    assert seen == [(1, None)]  # 只解读 1 条 keep, 且绕过缓存
    assert out["item_count"] == 1
    assert out["skipped_by_reason"] == {}
