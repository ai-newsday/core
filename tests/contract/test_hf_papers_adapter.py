import json
import logging
from datetime import datetime, timezone

import httpx
import respx

from src.adapters.sources.hf_papers import HFPapersAdapter
from src.core.types import RunContext, SourceSpec, Genre, Publisher


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.hfp"),
    )


def _spec():
    return SourceSpec(
        name="hf-papers",
        url="https://huggingface.co/api/papers",
        genre=Genre.paper, publisher=Publisher.company,
        adapter="hf_papers",
    )


@respx.mock
async def test_hf_papers_maps_fields():
    data = json.load(open("fixtures/sources/hf_papers_sample.json"))
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=data))
    items = await HFPapersAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "Diffusion Editing at Scale"
    assert it.link == "https://huggingface.co/papers/2605.00001"
    assert it.genre == Genre.paper
    # no submittedOnDailyAt in fixture -> falls back to publishedAt
    assert it.published_at == datetime(2026, 5, 30, 8, 0, tzinfo=timezone.utc)
    assert it.raw_summary == "A method for image editing."


@respx.mock
async def test_hf_papers_prefers_submitted_on_daily_at():
    """Featured papers count as fresh by their daily-feature date, not the old
    arxiv publishedAt — otherwise the collection time-window drops the whole set."""
    data = [
        {
            "paper": {
                "id": "2605.09999",
                "title": "Featured Today, Published Last Week",
                "publishedAt": "2026-05-20T00:00:00.000Z",  # old: would be window-filtered
                "submittedOnDailyAt": "2026-05-30T00:00:00.000Z",  # daily feature date (fresh)
                "summary": "S.",
                "upvotes": 88,
            }
        }
    ]
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=data))
    items = await HFPapersAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc)
    assert items[0].signals["upvotes"] == 88
