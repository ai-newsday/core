from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.core.types import Genre, Publisher, RawItem, SourceReport, SourceSpec


def _utc(h_ago=0):
    return datetime.now(timezone.utc) - timedelta(hours=h_ago)


def test_genre_publisher_enums_have_expected_values():
    assert {g.value for g in Genre} == {"paper", "model", "announcement", "writeup", "news"}
    assert {p.value for p in Publisher} == {"lab", "company", "individual", "media"}


def test_rawitem_minimal_valid():
    it = RawItem(
        title_en="GPT-X released",
        link="https://example.com/a",
        source="openai",
        genre=Genre.announcement,
        publisher=Publisher.lab,
        published_at=_utc(),
    )
    assert it.fetched_via == "native"
    assert it.raw_summary is None
    assert it.genre is Genre.announcement and it.publisher is Publisher.lab


def test_rawitem_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="x",
            link="https://e.com",
            source="s",
            genre=Genre.paper,
            publisher=Publisher.company,
            published_at=datetime(2026, 5, 30, 12, 0, 0),  # naive
        )


def test_rawitem_rejects_empty_required():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="",
            link="https://e.com",
            source="s",
            genre=Genre.paper,
            publisher=Publisher.company,
            published_at=_utc(),
        )


def test_rawitem_rejects_unknown_genre():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="x",
            link="https://e.com",
            source="s",
            genre="tool",  # not a valid Genre
            publisher=Publisher.company,
            published_at=_utc(),
        )


def test_sourcereport_status_literal():
    r = SourceReport(name="openai", status="working", item_count=3, elapsed_ms=120)
    assert r.error is None


def test_sourcespec_defaults():
    s = SourceSpec(
        name="hf-papers",
        url="https://huggingface.co/api/papers",
        genre="paper",
        publisher="company",
        adapter="hf_papers",
    )
    assert s.status == "working" and s.priority == 3 and s.needs_firecrawl is False
    assert s.genre is Genre.paper and s.publisher is Publisher.company
