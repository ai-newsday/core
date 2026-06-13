import json

import pytest
from pydantic import ValidationError

from src.core.config import load_review_config, load_review_decisions
from src.core.types import ReviewConfig, ReviewDecision


def test_load_review_config_missing_returns_defaults(tmp_path):
    cfg = load_review_config(str(tmp_path / "nope.yaml"))
    assert cfg == ReviewConfig()


def test_load_review_config_overrides_fields(tmp_path):
    p = tmp_path / "review.yaml"
    p.write_text(
        'title_max_chars: 40\nmin_evidence: 2\ndecisions_path: "x/y.json"\n', encoding="utf-8"
    )
    cfg = load_review_config(str(p))
    assert cfg.title_max_chars == 40 and cfg.min_evidence == 2
    assert cfg.decisions_path == "x/y.json"
    # 未覆盖字段保持默认
    assert cfg.summary_max_chars == 120 and cfg.tags_count == 3


def test_load_review_decisions_missing_returns_empty(tmp_path):
    assert load_review_decisions(str(tmp_path / "nope.json")) == {}


def test_load_review_decisions_parses_keyed_by_link(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(
        json.dumps(
            {
                "https://a/1": {"action": "drop"},
                "https://a/2": {"action": "edit", "order": 0, "edits": {"title": "新标题"}},
                "__daily_take__": {"action": "edit", "edits": {"daily_take": "人工看点"}},
            }
        ),
        encoding="utf-8",
    )
    out = load_review_decisions(str(p))
    assert set(out) == {"https://a/1", "https://a/2", "__daily_take__"}
    assert isinstance(out["https://a/1"], ReviewDecision)
    assert out["https://a/1"].action == "drop"
    assert out["https://a/2"].order == 0
    assert out["https://a/2"].edits["title"] == "新标题"


def test_load_review_decisions_rejects_unknown_action(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"https://a/1": {"action": "zap"}}), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_review_decisions(str(p))
