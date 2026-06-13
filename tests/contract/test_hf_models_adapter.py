import json
import logging
from datetime import datetime, timezone

import httpx
import respx

from src.adapters.sources.hf_models import HFModelsAdapter
from src.core.types import RunContext, SourceSpec, SourceType


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
        type=SourceType.MODEL,
        adapter="hf_models",
    )


@respx.mock
async def test_hf_models_uses_ctx_now_for_published_at():
    """hf-models 现用 sort=likes7d (trending 榜), 语义是'今日热门', 所以
    published_at = ctx.now, 不是创建时间。原 createdAt 进 signals.created_at。"""
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
    assert it.source_type == SourceType.MODEL
    assert it.published_at == ctx.now  # 用 now, 不是 createdAt
    assert it.signals.get("created_at") == "2026-05-30T09:30:00+00:00"  # 原信息保留
