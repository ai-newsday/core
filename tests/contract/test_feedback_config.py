import json

import pytest
from pydantic import ValidationError

from src.core.config import load_feedback_config, load_feedback_events, load_quality_weights
from src.core.types import FeedbackConfig


def test_load_feedback_config_missing_returns_defaults(tmp_path):
    cfg = load_feedback_config(str(tmp_path / "nope.yaml"))
    assert cfg == FeedbackConfig()


def test_load_feedback_config_overrides_fields(tmp_path):
    p = tmp_path / "feedback.yaml"
    p.write_text(
        'step: 0.1\nmin_events: 3\nedit_factor: 0.25\nevents_path: "x/ev.json"\n', encoding="utf-8"
    )
    cfg = load_feedback_config(str(p))
    assert cfg.step == 0.1 and cfg.min_events == 3
    assert cfg.edit_factor == 0.25 and cfg.events_path == "x/ev.json"
    # uncovered fields keep defaults
    assert cfg.baseline_weight == 1.0 and cfg.max_weight == 1.5


def test_load_feedback_events_missing_returns_empty(tmp_path):
    assert load_feedback_events(str(tmp_path / "none.json")) == []


def test_load_feedback_events_parses_and_validates(tmp_path):
    p = tmp_path / "ev.json"
    p.write_text(
        json.dumps(
            [
                {
                    "link": "https://a/1",
                    "source": "s",
                    "action": "keep",
                    "run_id": "r1",
                    "ts": "2026-05-30T12:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    evs = load_feedback_events(str(p))
    assert len(evs) == 1 and evs[0].action == "keep" and evs[0].source == "s"


def test_load_feedback_events_rejects_bad_action(tmp_path):
    p = tmp_path / "ev.json"
    p.write_text(
        json.dumps(
            [
                {
                    "link": "https://a/1",
                    "source": "s",
                    "action": "nope",
                    "run_id": "r1",
                    "ts": "2026-05-30T12:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_feedback_events(str(p))


def test_load_quality_weights_missing_returns_empty(tmp_path):
    assert load_quality_weights(str(tmp_path / "none.json")) == {}


def test_load_quality_weights_parses(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"src": 1.2, "other": 0.8}), encoding="utf-8")
    w = load_quality_weights(str(p))
    assert w == {"src": 1.2, "other": 0.8}
