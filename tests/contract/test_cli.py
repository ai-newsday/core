import json, logging
from datetime import datetime, timezone
import httpx, respx
from src.cli import run_dry


@respx.mock
def test_run_dry_returns_summary_dict(tmp_path):
    reg = tmp_path / "r.yaml"
    reg.write_text(
        '- {name: openai, url: "https://openai.com/news/rss.xml", '
        'type: official, adapter: rss, status: working, priority: 2}\n')
    xml = open("fixtures/sources/rss_sample.xml", "rb").read()
    respx.get("https://openai.com/news/rss.xml").mock(
        return_value=httpx.Response(200, content=xml))
    out = run_dry(registry_path=str(reg),
                  now=datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc))
    assert out["is_silent"] is False
    assert out["total_items"] == 1
    assert out["source_reports"][0]["name"] == "openai"
    json.dumps(out)                                  # must be JSON-serializable


from datetime import datetime, timezone
from src.cli import run_dry_dedup
from tests.fakes import FakeEmbeddingProvider


def test_run_dry_dedup_returns_dedupresult_json():
    out = run_dry_dedup(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),
    )
    assert "cluster_count" in out and "deduped_items" in out
    assert out["input_count"] == out["cluster_count"] + out["duplicate_count"]


from src.cli import run_dry_score


def test_run_dry_score_returns_scoreresult_json():
    out = run_dry_score(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),
    )
    assert "selected_count" in out and "selected_items" in out
    assert "quota_report" in out
    assert out["input_count"] >= out["selected_count"]
    json.dumps(out)                                  # must be JSON-serializable


from src.cli import run_dry_interpret
from tests.fakes import FailingLLMProvider


def test_run_dry_interpret_returns_result_json():
    out = run_dry_interpret(
        registry_path="tests/golden/data/registry_min.yaml",
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
    )
    assert "interpreted_count" in out and "interpreted_items" in out
    assert "daily_take" in out and "fallback_count" in out
    assert out["input_count"] >= 0
    json.dumps(out)                                  # must be JSON-serializable
