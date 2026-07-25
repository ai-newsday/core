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


def test_production_config_weighs_x_signals():
    # x_list adapter writes x_favorite/x_retweet/x_quote/x_reply into signals
    # (src/adapters/sources/x_list.py); popularity_weights must know about
    # them or every X item's "可见指标" silently stays 0 regardless of engagement.
    c = load_scoring_config("config/scoring.yaml")
    assert c.popularity_weights.get("x_favorite", 0) > 0
    assert c.popularity_weights.get("x_retweet", 0) > 0


def test_production_config_discounts_x_institutional_authority():
    # 2026-07-24 实测: X 推文套用跟官方博客/论文一样的固定"机构影响力"+内容矩阵分(78分),
    # 只给可见指标留 7 分浮动空间, 导致冷门推文(99分)和爆款推文(100分)几乎打平。
    # x_list 的机构影响力必须打折, 把空间让给可见指标/时效/关键词去真正区分内容。
    c = load_scoring_config("config/scoring.yaml")
    assert c.adapter_authority_factor.get("x_list", 1.0) == 0.0


def test_production_config_does_not_weigh_github_stars_in_visibility():
    # 2026-07-25 实测: github_stars 是仓库固定星数, 不是这次 release 的热度——同一仓库
    # 连续 3 个补丁版本(FunASR v1.3.27/28/29) github_stars 完全相同, 0.3*sqrt(stars)
    # 对任何 >~2500 星的仓库都会直接把可见指标顶满 15 分封顶, release_tier_score(真正
    # 衡量"这次发布重不重要"的信号)完全被盖过, 导致补丁版本跟真大版本一样顶到 100 分。
    c = load_scoring_config("config/scoring.yaml")
    assert "github_stars" not in c.popularity_weights


def test_card_pool_limit_default_and_override(tmp_path):
    assert ScoringConfig().card_pool_limit == 25
    p = tmp_path / "s.yaml"
    p.write_text("card_pool_limit: 40\n", encoding="utf-8")
    assert load_scoring_config(str(p)).card_pool_limit == 40
