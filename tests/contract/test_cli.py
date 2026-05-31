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
