import json
from datetime import datetime, timezone
from src.cli import run_dry_review
from tests.fakes import FakeEmbeddingProvider, FailingLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_review_returns_result_json(tmp_path):
    # 决策文件缺失 -> 全 keep/待审; registry_min 在离线下静默(0 条)
    out = run_dry_review(
        registry_path="tests/golden/data/registry_min.yaml", now=NOW,
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
        decisions_path=str(tmp_path / "nope.json"))
    assert "run_id" in out and out["now"] == NOW.isoformat()
    assert "kept_count" in out and "dropped_count" in out
    assert "edited_count" in out and "is_pending" in out
    assert "reviewed_items" in out and "daily_take" in out
    # 账目守恒(spec §8 不变量 1)
    assert (out["kept_count"] + out["edited_count"] + out["dropped_count"]
            == out["input_count"])
    json.dumps(out)                                  # must be JSON-serializable
