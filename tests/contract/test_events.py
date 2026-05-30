import json, logging
from src.observability.events import emit


def test_emit_logs_structured_json(caplog):
    logger = logging.getLogger("test.events")
    with caplog.at_level(logging.INFO, logger="test.events"):
        emit(logger, "source_fetch_success", name="openai", item_count=3)
    rec = caplog.records[-1]
    payload = json.loads(rec.message)
    assert payload["event"] == "source_fetch_success"
    assert payload["name"] == "openai"
    assert payload["item_count"] == 3
