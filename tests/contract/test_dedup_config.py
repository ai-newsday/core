from src.core.config import load_dedup_config
from src.core.registry import load_source_priorities


def test_load_dedup_config_reads_yaml():
    c = load_dedup_config("config/dedup.yaml")
    assert c.similarity_threshold == 0.83
    assert c.embedding_model == "Qwen/Qwen3-Embedding-8B"
    assert c.genre_rank == ["paper", "model", "announcement", "writeup", "news"]


def test_load_dedup_config_missing_file_returns_defaults():
    c = load_dedup_config("does/not/exist.yaml")
    assert c.similarity_threshold == 0.83


def test_load_source_priorities_maps_name_to_priority():
    m = load_source_priorities("tests/golden/data/registry_min.yaml")
    assert m == {"hf-papers": 1, "openai": 2, "some-blog": 3}


def test_load_source_priorities_missing_file_is_empty():
    assert load_source_priorities("does/not/exist.yaml") == {}


def test_load_source_priorities_skips_rows_missing_name(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- {url: x, priority: 1}\n- {name: ok, priority: 2}\n")
    assert load_source_priorities(str(p)) == {"ok": 2}
