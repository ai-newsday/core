import logging
from datetime import datetime, timezone

from src.adapters.vectorstore.memory import InMemoryVectorStore
from src.core.types import DedupConfig, RawItem, RunContext, Genre, Publisher
from tests.fakes import DEFAULT_PUBLISHER
from src.pipeline.dedup import build_embed_text, dedup
from tests.fakes import FailingEmbeddingProvider, FakeEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-dedup"))


def _item(title, link, source, st):
    return RawItem(title_en=title, link=link, source=source, genre=st, publisher=DEFAULT_PUBLISHER[st], published_at=NOW)


def _cfg():
    return DedupConfig(sources_registry_path="tests/golden/data/registry_min.yaml")


def test_golden_cross_source_merge():
    items = [
        _item("Event X", "https://a/1", "openai", Genre.announcement),
        _item("Event X take", "https://b/2", "some-blog", Genre.writeup),
        _item("Event X recap", "https://c/3", "some-blog", Genre.writeup),
    ]
    vecs = {
        build_embed_text(items[0]): [1.0, 0.0],
        build_embed_text(items[1]): [0.99, 0.10],
        build_embed_text(items[2]): [0.98, 0.12],
    }
    store = InMemoryVectorStore()
    res = dedup(items, _cfg(), _ctx(), embedder=FakeEmbeddingProvider(vecs), store=store)
    assert res.cluster_count == 1
    assert res.duplicate_count == 2
    assert res.deduped_items[0].source == "openai"
    assert sorted(res.deduped_items[0].related_links) == ["https://b/2", "https://c/3"]
    assert sum(c.size for c in res.clusters) == res.input_count == 3


def test_golden_no_duplicates():
    items = [
        _item("Alpha", "https://a/1", "openai", Genre.announcement),
        _item("Beta", "https://b/2", "openai", Genre.announcement),
        _item("Gamma", "https://c/3", "openai", Genre.announcement),
    ]
    vecs = {
        build_embed_text(items[0]): [1.0, 0.0, 0.0],
        build_embed_text(items[1]): [0.0, 1.0, 0.0],
        build_embed_text(items[2]): [0.0, 0.0, 1.0],
    }
    res = dedup(
        items, _cfg(), _ctx(), embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore()
    )
    assert res.cluster_count == 3 and res.duplicate_count == 0
    assert len(res.deduped_items) == res.cluster_count


def test_golden_primary_selection_official_over_blog():
    items = [
        _item("E blog", "https://b/1", "some-blog", Genre.writeup),
        _item("E official", "https://o/2", "openai", Genre.announcement),
    ]
    vecs = {build_embed_text(items[0]): [1.0, 0.02], build_embed_text(items[1]): [1.0, 0.0]}
    res = dedup(
        items, _cfg(), _ctx(), embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore()
    )
    assert res.cluster_count == 1
    assert res.deduped_items[0].source == "openai"
    assert res.deduped_items[0].related_links == ["https://b/1"]


def test_golden_threshold_boundary():
    items = [
        _item("P", "https://a/1", "openai", Genre.announcement),
        _item("P near", "https://b/2", "openai", Genre.announcement),
        _item("Q far", "https://c/3", "openai", Genre.announcement),
    ]
    vecs = {
        build_embed_text(items[0]): [1.0, 0.0],
        build_embed_text(items[1]): [0.95, 0.31],
        build_embed_text(items[2]): [0.0, 1.0],
    }
    res = dedup(
        items, _cfg(), _ctx(), embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore()
    )
    assert res.cluster_count == 2
    sizes = sorted(c.size for c in res.clusters)
    assert sizes == [1, 2]


def test_golden_duplicate_link_collapsed_first_wins():
    # Same link reaching dedup() (collect() does not guarantee link-uniqueness):
    # embedding_id = sha256(link) collides; without a guard the by_emb vector map
    # collapses to the last item's vector. Keep first, count as one input.
    items = [
        _item("First", "https://a/1", "openai", Genre.announcement),
        _item("Second dupe link", "https://a/1", "some-blog", Genre.writeup),
        _item("Other", "https://b/2", "openai", Genre.announcement),
    ]
    vecs = {
        build_embed_text(items[0]): [1.0, 0.0],
        build_embed_text(items[1]): [0.0, 1.0],
        build_embed_text(items[2]): [0.0, 1.0],
    }
    res = dedup(
        items, _cfg(), _ctx(), embedder=FakeEmbeddingProvider(vecs), store=InMemoryVectorStore()
    )
    assert res.input_count == 2
    links = {d.link for d in res.deduped_items}
    assert links == {"https://a/1", "https://b/2"}
    primary_a = next(d for d in res.deduped_items if d.link == "https://a/1")
    assert primary_a.title_en == "First"


def test_golden_empty_input():
    res = dedup([], _cfg(), _ctx(), embedder=FakeEmbeddingProvider({}), store=InMemoryVectorStore())
    assert res.clusters == [] and res.deduped_items == []
    assert res.input_count == res.cluster_count == res.duplicate_count == 0


def test_golden_embedding_degraded_all_singletons():
    items = [
        _item("A", "https://a/1", "openai", Genre.announcement),
        _item("B", "https://b/2", "openai", Genre.announcement),
    ]
    res = dedup(
        items, _cfg(), _ctx(), embedder=FailingEmbeddingProvider(), store=InMemoryVectorStore()
    )
    assert res.cluster_count == res.input_count == 2
    assert res.duplicate_count == 0
