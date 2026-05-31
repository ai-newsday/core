import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.adapters.sources.hf_models import HFModelsAdapter
from src.core.types import SourceSpec, SourceType, RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.hfm"))


def _spec():
    return SourceSpec(name="hf-models",
                      url="https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50",
                      type=SourceType.MODEL, adapter="hf_models")


@respx.mock
async def test_hf_models_uses_createdat():
    data = json.load(open("fixtures/sources/hf_models_sample.json"))
    respx.get(url__startswith="https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=data))
    items = await HFModelsAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "acme/diffusion-xl"
    assert it.link == "https://huggingface.co/acme/diffusion-xl"
    assert it.source_type == SourceType.MODEL
    assert it.published_at == datetime(2026, 5, 30, 9, 30, tzinfo=timezone.utc)  # createdAt, not lastModified
