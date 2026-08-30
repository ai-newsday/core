from src.core.config import load_storylink_config
from src.core.types import StoryLinkConfig


def test_missing_file_returns_defaults():
    c = load_storylink_config("does/not/exist.yaml")
    assert isinstance(c, StoryLinkConfig)
    assert c.enabled is True
    assert c.prompt_path == "src/prompts/story_link_confirm.md"
    assert c.temperature == 0.0


def test_loads_overrides(tmp_path):
    p = tmp_path / "storylink.yaml"
    p.write_text(
        "enabled: false\nentity_token_pattern: 'X\\d+'\nmax_tokens: 100\ntimeout_s: 10\n",
        encoding="utf-8",
    )
    c = load_storylink_config(str(p))
    assert c.enabled is False
    assert c.entity_token_pattern == "X\\d+"
    assert c.max_tokens == 100
    assert c.timeout_s == 10


def test_loads_providers(tmp_path):
    p = tmp_path / "storylink.yaml"
    p.write_text(
        "providers:\n"
        "  modelscope:\n"
        "    base_url: 'https://x/v1'\n"
        "    api_key_env: 'X_KEY'\n"
        "models: ['modelscope:foo']\n",
        encoding="utf-8",
    )
    c = load_storylink_config(str(p))
    assert c.providers["modelscope"].base_url == "https://x/v1"
    assert c.models == ["modelscope:foo"]


def test_production_config_exists_and_enabled():
    c = load_storylink_config("config/storylink.yaml")
    assert c.enabled is True
    assert c.entity_token_pattern
