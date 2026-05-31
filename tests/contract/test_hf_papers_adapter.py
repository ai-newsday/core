import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.core.types import SourceSpec, SourceType, RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
                      logger=logging.getLogger("test.hfp"))


def _spec():
    return SourceSpec(name="hf-papers", url="https://huggingface.co/api/papers",
                      type=SourceType.PAPER, adapter="hf_papers")


@respx.mock
async def test_hf_papers_maps_fields():
    data = json.load(open("fixtures/sources/hf_papers_sample.json"))
    respx.get("https://huggingface.co/api/papers").mock(
        return_value=httpx.Response(200, json=data))
    items = await HFPapersAdapter().fetch(_spec(), _ctx(), timeout_s=15)
    assert len(items) == 2
    it = items[0]
    assert it.title_en == "Diffusion Editing at Scale"
    assert it.link == "https://huggingface.co/papers/2605.00001"
    assert it.source_type == SourceType.PAPER
    assert it.published_at == datetime(2026, 5, 30, 8, 0, tzinfo=timezone.utc)
    assert it.raw_summary == "A method for image editing."
