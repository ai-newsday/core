import json
import logging
from datetime import datetime, timezone

import httpx
import respx

from src.adapters.sources.hf_models import HFModelsAdapter
from src.core.types import Genre, Publisher, RunContext, SourceSpec


def _ctx():
    return RunContext(
        run_id="t",
        now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        logger=logging.getLogger("test.hfm"),
    )


def _spec():
    return SourceSpec(
        name="hf-models",
        url="https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50",
        genre=Genre.model,
        publisher=Publisher.company,
        adapter="hf_models",
    )


@respx.mock
async def test_hf_models_uses_created_at_for_published_at():
    """published_at 用真实 createdAt(有则用, 无则回退 ctx.now)。
    >30 天的老模型被过滤掉, 解 FLUX.1-dev 被当今天发布重复推送的问题。"""
    data = json.load(open("fixtures/sources/hf_models_sample.json"))
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data)
    )
    ctx = _ctx()
    items = await HFModelsAdapter().fetch(_spec(), ctx, timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "acme/diffusion-xl"
    assert it.link == "https://huggingface.co/acme/diffusion-xl"
    assert it.genre == Genre.model
    assert it.published_at == datetime(2026, 5, 30, 9, 30, tzinfo=timezone.utc)
    assert it.signals.get("created_at") == "2026-05-30T09:30:00+00:00"  # 原信息保留
