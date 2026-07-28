"""golden: enrich_hf_models_readme 用伪 README 客户端, 验证素材填充 + 硬过滤 + 容错。"""

import asyncio
import logging
from datetime import datetime, timezone

from src.core.types import Genre, HFReadmeConfig, Publisher, RawItem, RunContext
from src.pipeline.hf_readme import enrich_hf_models_readme

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


class FakeReadmeClient:
    """注入式: model_id → README 原文(或 None = 404/无文件)。"""

    def __init__(self, mapping: dict[str, str | None]):
        self._map = mapping
        self.calls: list[str] = []

    async def fetch_readme(self, model_id: str) -> str | None:
        self.calls.append(model_id)
        return self._map.get(model_id)


class BoomClient:
    async def fetch_readme(self, model_id: str) -> str | None:
        raise RuntimeError("network down")


def _model_item(mid, adapter="hf_models"):
    return RawItem(
        title_en=mid,
        link=f"https://huggingface.co/{mid}",
        source="hf-models",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=None,
        adapter=adapter,
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-hf-readme"))


_REAL_README = "---\nlicense: mit\n---\n\n" + "This model does X and Y in production. " * 5


def test_non_hf_models_adapter_passthrough_no_client_call():
    items = [_model_item("a/b", adapter="rss")]
    client = FakeReadmeClient({})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert out == items
    assert client.calls == []


def test_real_readme_fills_raw_summary_and_keeps_item():
    items = [_model_item("microsoft/Mage-Flow")]
    client = FakeReadmeClient({"microsoft/Mage-Flow": _REAL_README})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert len(out) == 1
    assert out[0].raw_summary is not None
    assert "This model does X and Y" in out[0].raw_summary
    assert "license: mit" not in out[0].raw_summary  # frontmatter stripped


def test_missing_readme_filters_item_out():
    items = [_model_item("nobody/no-readme")]
    client = FakeReadmeClient({"nobody/no-readme": None})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert out == []


def test_readme_too_short_after_cleaning_filters_item_out():
    items = [_model_item("x/tiny")]
    client = FakeReadmeClient({"x/tiny": "---\nlicense: mit\n---\n\nHi."})
    out = asyncio.run(
        enrich_hf_models_readme(items, client, HFReadmeConfig(min_body_chars=80), _ctx())
    )
    assert out == []


def test_client_failure_filters_item_out_does_not_crash_batch():
    items = [_model_item("a/ok"), _model_item("b/boom")]

    class MixedClient:
        async def fetch_readme(self, model_id: str) -> str | None:
            if model_id == "b/boom":
                raise RuntimeError("network down")
            return _REAL_README

    out = asyncio.run(enrich_hf_models_readme(items, MixedClient(), HFReadmeConfig(), _ctx()))
    assert [i.title_en for i in out] == ["a/ok"]


def test_disabled_passthrough_no_client_call():
    items = [_model_item("a/b")]
    client = FakeReadmeClient({"a/b": _REAL_README})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(enabled=False), _ctx()))
    assert out == items
    assert client.calls == []


def test_mixed_list_preserves_order_for_kept_items():
    items = [
        _model_item("drop/me"),  # 无 README, 剔除
        _model_item("keep/me1"),
        RawItem(
            title_en="rss-item",
            link="https://blog.example.com/post",
            source="blog",
            genre=Genre.writeup,
            publisher=Publisher.individual,
            published_at=NOW,
            adapter="rss",
        ),  # 非 hf_models, 透传(不检查长度)
        _model_item("keep/me2"),
    ]
    client = FakeReadmeClient({"drop/me": None, "keep/me1": _REAL_README, "keep/me2": _REAL_README})
    out = asyncio.run(enrich_hf_models_readme(items, client, HFReadmeConfig(), _ctx()))
    assert [i.title_en for i in out] == ["keep/me1", "rss-item", "keep/me2"]
