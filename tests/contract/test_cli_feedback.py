import json
from datetime import datetime, timezone

from src.cli import run_dry_feedback
from tests.fakes import FailingLLMProvider, FakeEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_feedback_shape():
    out = run_dry_feedback(
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
        decisions_path="tests/golden/data/__no_such_decisions__.json",
    )
    for k in (
        "run_id",
        "now",
        "event_count",
        "source_count",
        "is_silent",
        "quality_weights",
        "weight_diff",
    ):
        assert k in out
    assert isinstance(out["quality_weights"], dict)
    assert isinstance(out["is_silent"], bool)
    # JSON 可序列化(weight_diff 的 tuple 会序列化成数组)
    json.dumps(out, ensure_ascii=False)
