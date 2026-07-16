from src.core.config import load_publish_config
from src.core.types import PublishConfig


def test_load_publish_config_missing_returns_defaults(tmp_path):
    cfg = load_publish_config(str(tmp_path / "nope.yaml"))
    assert cfg == PublishConfig()


def test_load_publish_config_overrides_fields(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text(
        'must_read_count: 5\ntop_keywords: 2\npending_watermark: "待审"\n', encoding="utf-8"
    )
    cfg = load_publish_config(str(p))
    assert cfg.must_read_count == 5 and cfg.top_keywords == 2
    assert cfg.pending_watermark == "待审"
    # 未覆盖字段保持默认
    assert cfg.genre_labels["model"] == "模型"


def test_load_publish_config_overrides_genre_labels(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text('genre_labels:\n  model: "大模型"\n  paper: "论文"\n', encoding="utf-8")
    cfg = load_publish_config(str(p))
    assert cfg.genre_labels == {"model": "大模型", "paper": "论文"}
    # 未覆盖标量字段保持默认
    assert cfg.must_read_count == 3


def test_publish_quota_total_limit_and_floor(tmp_path):
    d = PublishConfig()
    assert d.min_display_score == 40
    assert d.total_limit == 11
    assert d.quota["paper"] == 3

    p = tmp_path / "p.yaml"
    p.write_text(
        "min_display_score: 40\ntotal_limit: 5\nquota: {paper: 1, model: 1}\n",
        encoding="utf-8",
    )
    c = load_publish_config(str(p))
    assert c.total_limit == 5
    assert c.quota == {"paper": 1, "model": 1}


def test_load_publish_config_adapter_quota_default_empty():
    cfg = load_publish_config("does/not/exist.yaml")
    assert cfg.adapter_quota == {}


def test_load_publish_config_adapter_quota_override(tmp_path):
    p = tmp_path / "publish.yaml"
    p.write_text("adapter_quota: {github_releases: 2, github_trending: 1}\n", encoding="utf-8")
    cfg = load_publish_config(str(p))
    assert cfg.adapter_quota == {"github_releases": 2, "github_trending": 1}
