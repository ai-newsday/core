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
        encoding="utf-8",
    )
    c = load_scoring_config(str(p))
    assert c.fresh_hours == 12 and c.fresh_bonus == 20
    assert c.mid_hours == 36 and c.mid_bonus == 5
    assert c.stale_hours == 60 and c.stale_penalty == -15
    assert c.same_source_penalty == -8
    assert c.quota == {"paper": 1, "model": 1}
    assert c.total_limit == 5
    # untouched keys keep defaults
    assert c.genre_value["paper"]["一手性"] == 20
    assert c.publisher_authority["lab"] == 18


def test_load_scoring_config_reads_genre_and_publisher(tmp_path):
    p = tmp_path / "scoring.yaml"
    p.write_text(
        "genre_value: {paper: {一手性: 20}}\npublisher_authority: {lab: 18}\nquota: {paper: 2}\n",
        encoding="utf-8",
    )
    c = load_scoring_config(str(p))
    assert c.genre_value["paper"]["一手性"] == 20
    assert c.publisher_authority["lab"] == 18
    assert c.quota == {"paper": 2}


def test_repo_default_config_is_consistent():
    c = load_scoring_config("config/scoring.yaml")
    # 软配额: quota 是 per-genre 上限(和可 > total_limit), total_limit 是最终硬上限。
    # 不变量: total_limit 可达(<= 各上限之和), 且单类上限不超过 total_limit。
    assert c.total_limit <= sum(c.quota.values())
    assert all(q <= c.total_limit for q in c.quota.values())


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
