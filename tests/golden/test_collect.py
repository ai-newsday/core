import json
import logging
from datetime import datetime, timezone

import httpx
import respx

from src.core.types import CollectionConfig, RunContext
from src.pipeline.collect import collect

RSS_XML = open("fixtures/sources/rss_sample.xml", "rb").read()
HFP = json.load(open("fixtures/sources/hf_papers_sample.json"))


def _ctx(now):
    return RunContext(run_id="g", now=now, logger=logging.getLogger("golden"))


def _mount_ok():
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=HFP))
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )
    respx.get("https://deepmind.google/blog/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )


# Case 1 (spec §8.1): mixed sources incl. a 403 -> others succeed, no raise
@respx.mock
async def test_golden_mixed_with_403():
    _mount_ok()
    respx.get("https://broken.example/feed.xml").mock(return_value=httpx.Response(403))
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_golden.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    reps = {r.name: r for r in res.source_reports}
    assert set(reps) == {"hf-papers", "openai", "deepmind", "broken"}  # §7.4 enabled-only
    assert reps["broken"].status == "failed"
    assert any(r.status == "working" for r in res.source_reports)
    # §7.1 invariant: nothing older than max_window_hours
    cutoff = _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)).now
    from datetime import timedelta

    assert all(
        it.published_at >= cutoff - timedelta(hours=cfg.max_window_hours) for it in res.items
    )
    # §7.2 invariant: required fields present (pydantic guarantees; spot check)
    assert all(it.title_en and it.link and it.source for it in res.items)
    # §7.6 invariant
    assert all(it.fetched_via in ("native", "firecrawl") for it in res.items)


# Case 2 (spec §8.2): everything outside window -> silent
@respx.mock
async def test_golden_all_outside_window_is_silent():
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_allold.yaml")
    # now far in the future so the 2026-05-30 fixture item is outside 24h
    res = await collect(cfg, _ctx(datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)))
    assert res.items == [] and res.is_silent is True  # §7.5
    assert res.source_reports[0].status == "empty"


# Case 3 (spec §8.3): cross-source duplicates kept (no dedup in this layer)
@respx.mock
async def test_golden_cross_source_duplicates_kept():
    _mount_ok()
    respx.get("https://broken.example/feed.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )  # same content as openai/deepmind
    cfg = CollectionConfig(sources_registry_path="tests/golden/data/registry_golden.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    # openai + deepmind + broken each yield the same 1 dated RSS item -> 3 RSS items kept
    rss_items = [it for it in res.items if it.title_en == "Introducing GPT-X"]
    assert len(rss_items) == 3  # duplicates NOT removed here


# Case 5 (spec §8.5): registry missing -> fallback used, still produces items
@respx.mock
async def test_golden_registry_missing_uses_fallback():
    respx.get("https://huggingface.co/api/papers").mock(return_value=httpx.Response(200, json=HFP))
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )
    respx.get("https://deepmind.google/blog/rss.xml").mock(
        return_value=httpx.Response(200, content=RSS_XML)
    )
    cfg = CollectionConfig(sources_registry_path="does/not/exist.yaml")
    res = await collect(cfg, _ctx(datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    assert {r.name for r in res.source_reports} == {"hf-papers", "openai", "deepmind"}
    assert len(res.items) >= 1
