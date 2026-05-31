from datetime import datetime, timezone
from src.core.types import RawItem, SourceType
from src.pipeline.dedup import build_embed_text, embedding_id, _cosine


def _raw(title, summary=None, link="https://e.com/a"):
    return RawItem(title_en=title, link=link, source="s",
                   source_type=SourceType.OFFICIAL,
                   published_at=datetime(2026, 5, 30, tzinfo=timezone.utc),
                   raw_summary=summary)


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
