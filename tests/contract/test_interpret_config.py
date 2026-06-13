from src.core.config import load_interpret_config
from src.core.types import InterpretConfig


def test_missing_file_returns_defaults():
    c = load_interpret_config("does/not/exist.yaml")
    assert isinstance(c, InterpretConfig)
    assert c.tags_count == 3 and c.min_evidence == 1


def test_loads_overrides(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(
        "model: my-model\n"
        "temperature: 0.0\n"
        "max_tokens: 500\n"
        "timeout_s: 30\n"
        "title_max_chars: 50\n"
        "summary_max_chars: 100\n"
        "tags_count: 2\n"
        "min_evidence: 2\n"
        "item_prompt_path: a.md\n"
        "daily_prompt_path: b.md\n",
        encoding="utf-8",
    )
    c = load_interpret_config(str(p))
    assert c.model == "my-model" and c.temperature == 0.0
    assert c.max_tokens == 500 and c.timeout_s == 30
    assert c.title_max_chars == 50 and c.summary_max_chars == 100
    assert c.tags_count == 2 and c.min_evidence == 2
    assert c.item_prompt_path == "a.md" and c.daily_prompt_path == "b.md"


def test_repo_default_config_loads():
    c = load_interpret_config("config/interpret.yaml")
    assert c.tags_count == 3 and c.title_max_chars == 64
