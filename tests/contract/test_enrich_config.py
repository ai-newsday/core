from src.core.config import load_enrich_config
from src.core.types import EnrichConfig


def test_load_enrich_config_missing_returns_defaults(tmp_path):
    cfg = load_enrich_config(str(tmp_path / "nope.yaml"))
    assert cfg == EnrichConfig()


def test_load_enrich_config_overrides(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text(
        "enabled: false\nconcurrency: 3\ntimeout_s: 5\nskip_genres: [paper, model]\n",
        encoding="utf-8",
    )
    cfg = load_enrich_config(str(p))
    assert cfg.enabled is False and cfg.concurrency == 3 and cfg.timeout_s == 5
    assert cfg.skip_genres == ["paper", "model"]


def test_enrich_config_defaults_shape():
    c = EnrichConfig()
    assert c.enabled is True
    assert c.concurrency >= 1
    assert c.timeout_s > 0
    assert "paper" in c.skip_genres or "model" in c.skip_genres
