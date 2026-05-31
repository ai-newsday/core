import logging
from datetime import datetime, timezone
from src.core.registry import load_registry, FALLBACK_SOURCES
from src.core.types import RunContext


def _ctx():
    return RunContext(run_id="t", now=datetime.now(timezone.utc),
                      logger=logging.getLogger("test.registry"))


def test_load_returns_only_working_sources():
    specs = load_registry("tests/golden/data/registry_min.yaml", _ctx())
    names = {s.name for s in specs}
    assert names == {"hf-papers", "openai"}        # 'some-blog' is manual -> excluded


def test_missing_file_falls_back_and_warns(caplog):
    with caplog.at_level(logging.INFO, logger="test.registry"):
        specs = load_registry("does/not/exist.yaml", _ctx())
    assert specs == FALLBACK_SOURCES
    assert any("registry_load_failed" in r.message for r in caplog.records)


def test_fallback_sources_are_all_working():
    assert FALLBACK_SOURCES
    assert all(s.status == "working" for s in FALLBACK_SOURCES)
