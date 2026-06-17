from src.core.config import load_selfcheck_config


def test_missing_file_returns_defaults():
    cfg = load_selfcheck_config("config/does-not-exist.yaml")
    assert cfg.temperature == 0.0 and cfg.max_flags_per_item == 3
    assert cfg.fallback_models == []  # default empty


def test_fallback_models_loaded_from_yaml(tmp_path):
    p = tmp_path / "selfcheck.yaml"
    p.write_text(
        'model: A\nfallback_models:\n  - "B"\n  - "C"\n',
        encoding="utf-8",
    )
    cfg = load_selfcheck_config(str(p))
    assert cfg.model == "A" and cfg.fallback_models == ["B", "C"]


def test_overrides_from_yaml(tmp_path):
    p = tmp_path / "selfcheck.yaml"
    p.write_text(
        "model: M\ntemperature: 0.2\nmax_flags_per_item: 5\nmessage_max_chars: 80\n",
        encoding="utf-8",
    )
    cfg = load_selfcheck_config(str(p))
    assert cfg.model == "M" and cfg.temperature == 0.2
    assert cfg.max_flags_per_item == 5 and cfg.message_max_chars == 80
    assert cfg.tags_count == 3  # untouched field keeps default
