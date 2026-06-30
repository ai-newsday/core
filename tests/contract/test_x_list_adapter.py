import logging
from datetime import datetime, timezone

from src.adapters.sources import ADAPTERS
from src.adapters.sources.x_list import XListAdapter
from src.core.types import Genre, Publisher, RunContext, SourceSpec


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 6, 30, 1, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.x_list"),
    )


def _spec(list_id="L1", name="x-ai-lab", publisher=Publisher.lab, genre=Genre.announcement):
    return SourceSpec(
        name=name,
        url=f"xlist:{list_id}",
        genre=genre,
        publisher=publisher,
        adapter="x_list",
        status="manual",
    )


def test_x_list_is_registered_in_adapters():
    assert "x_list" in ADAPTERS
    assert isinstance(ADAPTERS["x_list"], XListAdapter)


async def test_x_list_empty_when_no_data_dir(tmp_path):
    adapter = XListAdapter(data_dir=tmp_path)
    items = await adapter.fetch(_spec(), _ctx(), timeout_s=15)
    assert items == []
