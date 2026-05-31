from src.core.config import load_scoring_config
from src.core.types import ScoringConfig


def test_missing_file_returns_defaults():
    c = load_scoring_config("does/not/exist.yaml")
    assert isinstance(c, ScoringConfig)
    assert c.total_limit == 8
    assert c.quota["paper"] == 2


def test_loads_and_flattens_nested_recency_and_penalty(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "recency:\n"
        "  fresh_hours: 12\n"
        "  fresh_bonus: 20\n"
        "  mid_hours: 36\n"
        "  mid_bonus: 5\n"
        "  stale_hours: 60\n"
        "  stale_penalty: -15\n"
        "penalty:\n"
        "  same_source: -8\n"
        "quota: {paper: 1, model: 1}\n"
        "total_limit: 5\n",
        encoding="utf-8")
    c = load_scoring_config(str(p))
    assert c.fresh_hours == 12 and c.fresh_bonus == 20
    assert c.mid_hours == 36 and c.mid_bonus == 5
    assert c.stale_hours == 60 and c.stale_penalty == -15
    assert c.same_source_penalty == -8
    assert c.quota == {"paper": 1, "model": 1}
    assert c.total_limit == 5
    # untouched keys keep defaults
    assert c.dimension_scores["official"]["一手性"] == 20


def test_repo_default_config_is_consistent():
    c = load_scoring_config("config/scoring.yaml")
    # invariant: sum of quotas must not exceed the hard total limit (spec §5.4)
    assert sum(c.quota.values()) <= c.total_limit
