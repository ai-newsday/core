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


from src.core.types import ProviderSpec, ReleaseImportanceConfig


def test_enrich_config_release_importance_defaults():
    cfg = EnrichConfig()
    ri = cfg.release_importance
    assert isinstance(ri, ReleaseImportanceConfig)
    assert ri.enabled is True
    assert ri.hard_filter_max_tier == 1
    assert ri.tier_score == {2: 4.0, 3: 9.0}
    assert ri.empty_body_min_chars == 30
    assert ri.prompt_path == "src/prompts/release_importance.md"
    assert "modelscope" in ri.providers


def test_load_enrich_config_release_importance_overrides(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text(
        """
release_importance:
  enabled: false
  models: ["modelscope:deepseek-ai/DeepSeek-V4-Flash"]
  fallback_models: ["agnes:agnes-2.0-flash"]
  temperature: 0.1
  max_tokens: 300
  timeout_s: 20
  empty_body_min_chars: 40
  hard_filter_max_tier: 2
  tier_score: {2: 5, 3: 10}
  prompt_path: "src/prompts/release_importance.md"
  providers:
    modelscope:
      base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
      api_key_env: "MODELSCOPE_API_KEY"
    agnes:
      base_url: "https://apihub.agnes-ai.com/v1/chat/completions"
      api_key_env: "AGNES_API_KEY"
""",
        encoding="utf-8",
    )
    cfg = load_enrich_config(str(p))
    ri = cfg.release_importance
    assert ri.enabled is False
    assert ri.models == ["modelscope:deepseek-ai/DeepSeek-V4-Flash"]
    assert ri.fallback_models == ["agnes:agnes-2.0-flash"]
    assert ri.timeout_s == 20
    assert ri.empty_body_min_chars == 40
    assert ri.hard_filter_max_tier == 2
    assert ri.tier_score == {2: 5, 3: 10}
    assert set(ri.providers.keys()) == {"modelscope", "agnes"}
    assert isinstance(ri.providers["agnes"], ProviderSpec)
    assert ri.providers["agnes"].api_key_env == "AGNES_API_KEY"


def test_load_enrich_config_release_importance_missing_block_uses_defaults(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text("enabled: true\n", encoding="utf-8")
    cfg = load_enrich_config(str(p))
    assert cfg.release_importance == ReleaseImportanceConfig()


def test_production_enrich_yaml_has_release_importance_configured():
    cfg = load_enrich_config("config/enrich.yaml")
    ri = cfg.release_importance
    assert ri.enabled is True
    assert len(ri.models) >= 1
    assert ri.prompt_path == "src/prompts/release_importance.md"
