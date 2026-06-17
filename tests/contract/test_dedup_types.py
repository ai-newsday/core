from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.core.types import Cluster, DedupConfig, DedupResult, Genre, NewsItem, RawItem
from tests.fakes import DEFAULT_PUBLISHER


def _raw(title="GPT-X released", link="https://e.com/a", src="openai", st=Genre.announcement):
    return RawItem(
        title_en=title,
        link=link,
        source=src,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )


def test_newsitem_inherits_rawitem_and_adds_fields():
    raw = _raw()
    ni = NewsItem(
        **raw.model_dump(),
        cluster_id="evt-2026-05-30-001",
        related_links=["https://e.com/b"],
        embedding_id="abc123",
    )
    assert ni.title_en == "GPT-X released"
    assert ni.cluster_id == "evt-2026-05-30-001"
    assert ni.related_links == ["https://e.com/b"]
    assert ni.embedding_id == "abc123"


def test_newsitem_rejects_empty_cluster_id():
    raw = _raw()
    with pytest.raises(ValidationError):
        NewsItem(**raw.model_dump(), cluster_id="")


def test_newsitem_defaults():
    raw = _raw()
    ni = NewsItem(**raw.model_dump(), cluster_id="evt-2026-05-30-001")
    assert ni.related_links == [] and ni.embedding_id is None


def test_dedupconfig_defaults():
    c = DedupConfig()
    assert c.similarity_threshold == 0.83
    assert c.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert c.batch_size == 32
    assert c.genre_rank[0] == "paper"
    assert c.sources_registry_path == "config/sources.yaml"


def test_cluster_and_result_construct():
    raw = _raw()
    primary = NewsItem(**raw.model_dump(), cluster_id="evt-2026-05-30-001")
    cl = Cluster(
        cluster_id="evt-2026-05-30-001", primary=primary, members=[raw], related_links=[], size=1
    )
    res = DedupResult(
        clusters=[cl], deduped_items=[primary], input_count=1, cluster_count=1, duplicate_count=0
    )
    assert res.cluster_count == 1 and res.duplicate_count == 0
