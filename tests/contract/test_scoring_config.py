from src.core.config import load_scoring_config
from src.core.types import ScoringConfig


def test_missing_file_returns_defaults():
    c = load_scoring_config("does/not/exist.yaml")
    assert isinstance(c, ScoringConfig)
    assert c.card_pool_limit == 25


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
        "  same_source: -8\n",
        encoding="utf-8",
    )
    c = load_scoring_config(str(p))
    assert c.fresh_hours == 12 and c.fresh_bonus == 20
    assert c.mid_hours == 36 and c.mid_bonus == 5
    assert c.stale_hours == 60 and c.stale_penalty == -15
    assert c.same_source_penalty == -8
    # untouched keys keep defaults
    assert c.genre_value["paper"]["一手性"] == 20
    assert c.publisher_authority["lab"] == 18


def test_load_scoring_config_reads_genre_and_publisher(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "genre_value: {paper: {一手性: 20}}\npublisher_authority: {lab: 18}\n",
        encoding="utf-8",
    )
    c = load_scoring_config(str(p))
    assert c.genre_value["paper"]["一手性"] == 20
    assert c.publisher_authority["lab"] == 18


def test_loads_topic_boost(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "topic_boost:\n  keywords:\n    - multimodal\n    - agent\n  bonus: 8\n", encoding="utf-8"
    )
    c = load_scoring_config(str(p))
    assert c.topic_keywords == ["multimodal", "agent"]
    assert c.topic_bonus == 8


def test_missing_topic_boost_uses_defaults():
    c = load_scoring_config("does/not/exist.yaml")
    assert c.topic_keywords == []
    assert c.topic_bonus == 5.0


def test_production_config_has_topic_keywords():
    c = load_scoring_config("config/scoring.yaml")
    assert len(c.topic_keywords) > 0
    assert c.topic_bonus > 0
    assert "multimodal" in c.topic_keywords


def test_card_pool_limit_default_and_override(tmp_path):
    assert ScoringConfig().card_pool_limit == 25
    p = tmp_path / "s.yaml"
    p.write_text("card_pool_limit: 40\n", encoding="utf-8")
    assert load_scoring_config(str(p)).card_pool_limit == 40
