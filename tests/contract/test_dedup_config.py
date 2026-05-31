import logging
from src.core.config import load_dedup_config
from src.core.registry import load_source_priorities
from src.core.types import RunContext
from datetime import datetime, timezone


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, tzinfo=timezone.utc),
                      logger=logging.getLogger("t"))


def test_load_dedup_config_reads_yaml():
    c = load_dedup_config("config/dedup.yaml")
    assert c.similarity_threshold == 0.83
    assert c.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert c.source_type_rank == [
        "official", "paper", "model", "tool", "news", "community", "blog"]


def test_load_dedup_config_missing_file_returns_defaults():
    c = load_dedup_config("does/not/exist.yaml")
    assert c.similarity_threshold == 0.83


def test_load_source_priorities_maps_name_to_priority():
    m = load_source_priorities("tests/golden/data/registry_min.yaml")
    assert m == {"hf-papers": 1, "openai": 2, "some-blog": 3}


def test_load_source_priorities_missing_file_is_empty():
    assert load_source_priorities("does/not/exist.yaml") == {}
