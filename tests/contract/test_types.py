from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.core.types import RawItem, SourceReport, SourceSpec, SourceType


def _utc(h_ago=0):
    return datetime.now(timezone.utc) - timedelta(hours=h_ago)


def test_rawitem_minimal_valid():
    it = RawItem(
        title_en="GPT-X released",
        link="https://example.com/a",
        source="openai",
        source_type=SourceType.OFFICIAL,
        published_at=_utc(),
    )
    assert it.fetched_via == "native"
    assert it.raw_summary is None


def test_rawitem_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="x",
            link="https://e.com",
            source="s",
            source_type=SourceType.PAPER,
            published_at=datetime(2026, 5, 30, 12, 0, 0),  # naive
        )


def test_rawitem_rejects_empty_required():
    with pytest.raises(ValidationError):
        RawItem(
            title_en="",
            link="https://e.com",
            source="s",
            source_type=SourceType.PAPER,
            published_at=_utc(),
        )


def test_blog_is_valid_source_type():
    assert SourceType("blog") == SourceType.BLOG


def test_sourcereport_status_literal():
    r = SourceReport(name="openai", status="working", item_count=3, elapsed_ms=120)
    assert r.error is None


def test_sourcespec_defaults():
    s = SourceSpec(
        name="hf-papers",
        url="https://huggingface.co/api/papers",
        type=SourceType.PAPER,
        adapter="hf_papers",
    )
    assert s.status == "working" and s.priority == 3 and s.needs_firecrawl is False
