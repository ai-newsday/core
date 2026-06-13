import json
from datetime import datetime, timezone

from src.cli import run_dry_publish
from tests.fakes import FailingLLMProvider, FakeEmbeddingProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def test_run_dry_publish_shape():
    out = run_dry_publish(
        registry_path="tests/golden/data/registry_min.yaml",
        now=NOW,
        embedder=FakeEmbeddingProvider({}),
        llm=FailingLLMProvider(),
        decisions_path="tests/golden/data/__no_such_decisions__.json",
    )
    # 形状
    for k in (
        "run_id",
        "now",
        "input_count",
        "must_read_count",
        "item_count",
        "is_pending",
        "is_silent",
        "markdown",
    ):
        assert k in out
    assert isinstance(out["markdown"], str)
    assert isinstance(out["is_pending"], bool)
    # JSON 可序列化
    json.dumps(out, ensure_ascii=False)
