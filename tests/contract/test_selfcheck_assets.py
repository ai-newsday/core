from src.core.config import load_selfcheck_config
from src.core.prompts import load_prompt


def test_config_file_loads_and_points_to_existing_prompt():
    cfg = load_selfcheck_config("config/selfcheck.yaml")
    body = load_prompt(cfg.prompt_path)
    # prompt must expose the placeholders the builder substitutes
    for ph in ("{{takeaway}}", "{{hot_take}}", "{{raw_summary}}", "{{evidence}}"):
        assert ph in body
    # critic must be told to output the two-key JSON structure
    assert "consistency" in body and "ai_slop" in body
