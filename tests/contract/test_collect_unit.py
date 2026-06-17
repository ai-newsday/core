import logging
from datetime import datetime, timedelta, timezone

import pytest

from src.core.types import CollectionConfig, RawItem, RunContext, SourceSpec, Genre, Publisher
from src.pipeline import collect as collect_mod

NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="t", now=NOW, logger=logging.getLogger("test.collect"))


def _item(name, hours_ago):
    return RawItem(
        title_en=f"t-{name}",
        link=f"https://e.com/{name}-{hours_ago}",
        source=name,
        genre=Genre.announcement, publisher=Publisher.lab,
        published_at=NOW - timedelta(hours=hours_ago),
    )


class FakeOK:
    def __init__(self, items):
        self._items = items

    async def fetch(self, source, ctx, timeout_s):
        return self._items


class FakeBoom:
    async def fetch(self, source, ctx, timeout_s):
        raise RuntimeError("403 Forbidden")


@pytest.fixture
def cfg(tmp_path):
    return CollectionConfig(sources_registry_path=str(tmp_path / "x.yaml"))


async def test_window_filter_drops_old_items(monkeypatch, cfg):
    specs = [SourceSpec(name="a", url="u", genre=Genre.announcement, publisher=Publisher.lab, adapter="rss")]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS", {"rss": FakeOK([_item("a", 2), _item("a", 100)])})
    res = await collect_mod.collect(cfg, _ctx())
    assert len(res.items) == 1  # 100h-old dropped (72h window)
    assert res.is_silent is False
    rep = res.source_reports[0]
    assert rep.status == "working" and rep.item_count == 1


async def test_one_source_failure_does_not_break_chain(monkeypatch, cfg):
    specs = [
        SourceSpec(name="a", url="u", genre=Genre.announcement, publisher=Publisher.lab, adapter="rss"),
        SourceSpec(name="b", url="u", genre=Genre.announcement, publisher=Publisher.lab, adapter="hf_papers"),
    ]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(
        collect_mod, "ADAPTERS", {"rss": FakeOK([_item("a", 1)]), "hf_papers": FakeBoom()}
    )
    res = await collect_mod.collect(cfg, _ctx())
    assert len(res.items) == 1
    reps = {r.name: r for r in res.source_reports}
    assert reps["a"].status == "working"
    assert reps["b"].status == "failed" and "403" in reps["b"].error
    assert len(res.source_reports) == 2  # invariant: every enabled source reported


async def test_empty_source_marked_empty_not_failed(monkeypatch, cfg):
    specs = [SourceSpec(name="a", url="u", genre=Genre.announcement, publisher=Publisher.lab, adapter="rss")]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS", {"rss": FakeOK([])})
    res = await collect_mod.collect(cfg, _ctx())
    assert res.is_silent is True and res.items == []
    assert res.source_reports[0].status == "empty"


async def test_needs_firecrawl_skipped_when_disabled(monkeypatch, cfg):
    specs = [
        SourceSpec(name="hard", url="u", genre=Genre.writeup, publisher=Publisher.individual, adapter="rss", needs_firecrawl=True)
    ]
    monkeypatch.setattr(collect_mod, "load_registry", lambda p, c: specs)
    monkeypatch.setattr(collect_mod, "ADAPTERS", {"rss": FakeOK([_item("hard", 1)])})
    res = await collect_mod.collect(cfg, _ctx())  # firecrawl_enabled defaults False
    assert res.source_reports[0].status == "failed"
    assert "firecrawl" in res.source_reports[0].error
    assert res.items == []
