import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import respx

from src.adapters.sources.hf_models import _TRENDING_MAX_AGE_DAYS, HFModelsAdapter
from src.core.types import RunContext, SourceSpec, SourceType


def _ctx(now=None):
    return RunContext(run_id="t",
                      now=now or datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.hfm"))


def _spec():
    return SourceSpec(name="hf-models",
                      url="https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50",
                      type=SourceType.MODEL, adapter="hf_models")


@respx.mock
async def test_hf_models_uses_created_at_for_published_at():
    """published_at uses real createdAt when available."""
    data = json.load(open("fixtures/sources/hf_models_sample.json"))
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    ctx = _ctx()
    items = await HFModelsAdapter().fetch(_spec(), ctx, timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "acme/diffusion-xl"
    assert it.link == "https://huggingface.co/acme/diffusion-xl"
    assert it.source_type == SourceType.MODEL
    assert it.published_at == datetime(2026, 5, 30, 9, 30, tzinfo=timezone.utc)
    assert it.signals.get("created_at") == "2026-05-30T09:30:00+00:00"


@respx.mock
async def test_hf_models_filters_old_models():
    """Models older than _TRENDING_MAX_AGE_DAYS are excluded."""
    old_date = "2025-01-01T00:00:00.000Z"
    fresh_date = "2026-05-29T10:00:00.000Z"
    data = [
        {"id": "old/model", "createdAt": old_date, "likes": 9999},
        {"id": "fresh/model", "createdAt": fresh_date, "likes": 10},
    ]
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    ctx = _ctx()
    items = await HFModelsAdapter().fetch(_spec(), ctx, timeout_s=15)
    assert len(items) == 1
    assert items[0].title_en == "fresh/model"


@respx.mock
async def test_hf_models_age_boundary():
    """Model exactly at the 30-day boundary is included; one day over is excluded."""
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    exactly_30d = (now - timedelta(days=_TRENDING_MAX_AGE_DAYS)).isoformat()
    over_30d = (now - timedelta(days=_TRENDING_MAX_AGE_DAYS + 1)).isoformat()
    data = [
        {"id": "boundary/model", "createdAt": exactly_30d, "likes": 5},
        {"id": "too-old/model", "createdAt": over_30d, "likes": 5},
    ]
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    items = await HFModelsAdapter().fetch(_spec(), _ctx(now), timeout_s=15)
    assert len(items) == 1
    assert items[0].title_en == "boundary/model"


@respx.mock
async def test_hf_models_no_created_at_fallback():
    """Model with no createdAt uses ctx.now as published_at and is not filtered."""
    data = [{"id": "no-date/model", "likes": 42}]
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    ctx = _ctx()
    items = await HFModelsAdapter().fetch(_spec(), ctx, timeout_s=15)
    assert len(items) == 1
    assert items[0].published_at == ctx.now
