from datetime import datetime, timezone

from src.core.types import Genre, Publisher, RawItem
from src.pipeline.dedup import _cosine, build_embed_text, embedding_id
from tests.fakes import DEFAULT_PUBLISHER


def _raw(title, summary=None, link="https://e.com/a"):
    return RawItem(
        title_en=title,
        link=link,
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        published_at=datetime(2026, 5, 30, tzinfo=timezone.utc),
        raw_summary=summary,
    )


def test_build_embed_text_with_summary():
    assert build_embed_text(_raw("T", "S")) == "T\nS"


def test_build_embed_text_without_summary():
    assert build_embed_text(_raw("T", None)) == "T"


def test_embedding_id_is_stable_16_hex():
    a = embedding_id("https://e.com/a")
    b = embedding_id("https://e.com/a")
    assert a == b and len(a) == 16


def test_cosine_orthogonal_is_zero_and_parallel_is_one():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert abs(_cosine([1.0, 1.0], [2.0, 2.0]) - 1.0) < 1e-9


def test_cosine_zero_vector_is_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


import logging

from src.core.types import DedupConfig, RunContext
from src.pipeline.dedup import cluster


def _ctx():
    return RunContext(
        run_id="t", now=datetime(2026, 5, 30, tzinfo=timezone.utc), logger=logging.getLogger("t")
    )


def _item(title, link, source, st, when=None):
    return RawItem(
        title_en=title,
        link=link,
        source=source,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=when or datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )


def test_cluster_merges_similar_above_threshold():
    items = [
        _item("A", "https://e/1", "openai", Genre.announcement),
        _item("B", "https://e/2", "blogx", Genre.writeup),
    ]
    vectors = [[1.0, 0.0], [0.99, 0.14]]
    cfg = DedupConfig()
    clusters = cluster(items, vectors, {"openai": 2, "blogx": 3}, cfg, _ctx())
    assert len(clusters) == 1
    c = clusters[0]
    assert c.size == 2
    assert c.primary.source == "openai"
    assert c.related_links == ["https://e/2"]
    assert c.cluster_id == "evt-2026-05-30-001"


def test_cluster_keeps_dissimilar_separate():
    items = [
        _item("A", "https://e/1", "openai", Genre.announcement),
        _item("B", "https://e/2", "openai", Genre.announcement),
    ]
    vectors = [[1.0, 0.0], [0.0, 1.0]]
    clusters = cluster(items, vectors, {"openai": 2}, DedupConfig(), _ctx())
    assert len(clusters) == 2
    assert [c.cluster_id for c in clusters] == ["evt-2026-05-30-001", "evt-2026-05-30-002"]
    assert all(c.size == 1 and c.related_links == [] for c in clusters)


def test_cluster_primary_priority_then_published():
    early = datetime(2026, 5, 30, 8, tzinfo=timezone.utc)
    late = datetime(2026, 5, 30, 20, tzinfo=timezone.utc)
    items = [
        _item("low-prio", "https://e/1", "src-a", Genre.paper, late),
        _item("hi-prio", "https://e/2", "src-b", Genre.paper, late),
        _item("earliest", "https://e/3", "src-b", Genre.paper, early),
    ]
    vectors = [[1.0, 0.0], [1.0, 0.01], [1.0, 0.02]]
    clusters = cluster(items, vectors, {"src-a": 2, "src-b": 1}, DedupConfig(), _ctx())
    assert len(clusters) == 1
    assert clusters[0].primary.title_en == "earliest"


def test_cluster_none_vector_is_forced_singleton():
    items = [
        _item("A", "https://e/1", "openai", Genre.announcement),
        _item("B", "https://e/2", "openai", Genre.announcement),
    ]
    vectors = [None, None]
    clusters = cluster(items, vectors, {"openai": 2}, DedupConfig(), _ctx())
    assert len(clusters) == 2


def test_cluster_sets_embedding_id_on_primary():
    items = [_item("A", "https://e/1", "openai", Genre.announcement)]
    clusters = cluster(items, [[1.0]], {"openai": 2}, DedupConfig(), _ctx())
    assert clusters[0].primary.embedding_id == embedding_id("https://e/1")
