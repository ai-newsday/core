import json
import logging
from datetime import datetime, timezone

import pytest

from src.adapters.vectorstore.memory import InMemoryVectorStore
from src.core.types import DedupConfig, RawItem, RunContext, Genre, Publisher
from src.pipeline.dedup import build_embed_text, dedup
from tests.fakes import FakeEmbeddingProvider, MisalignedEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx(name):
    return RunContext(run_id="t", now=NOW, logger=logging.getLogger(name))


def _item(title, link):
    return RawItem(
        title_en=title,
        link=link,
        source="openai",
        genre=Genre.announcement, publisher=Publisher.lab,
        published_at=NOW,
    )


def _cfg():
    return DedupConfig(sources_registry_path="tests/golden/data/registry_min.yaml")


@pytest.mark.parametrize("delta", [-1, 1])
def test_misaligned_embed_degrades_to_singletons(delta, caplog):
    items = [_item("A", "https://a/1"), _item("B", "https://b/2")]
    logger_name = f"dedup-misalign-{delta}"
    with caplog.at_level(logging.INFO, logger=logger_name):
        res = dedup(
            items,
            _cfg(),
            _ctx(logger_name),
            embedder=MisalignedEmbeddingProvider(delta=delta),
            store=InMemoryVectorStore(),
        )

    # spec §7: batch failure -> every item its own singleton, no crash
    assert res.cluster_count == res.input_count == 2
    assert res.duplicate_count == 0

    events = [json.loads(r.message)["event"] for r in caplog.records]
    assert "dedup_embedding_degraded" in events


def test_aligned_embed_does_not_degrade():
    items = [_item("A", "https://a/1"), _item("B", "https://b/2")]
    vecs = {build_embed_text(items[0]): [1.0, 0.0], build_embed_text(items[1]): [1.0, 0.0]}
    res = dedup(
        items,
        _cfg(),
        _ctx("dedup-aligned"),
        embedder=FakeEmbeddingProvider(vecs),
        store=InMemoryVectorStore(),
    )
    # similar vectors merge -> proves vectors were actually used, not discarded
    assert res.cluster_count == 1
