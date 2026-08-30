from src.core.prompts import load_prompt


def test_story_link_confirm_prompt_has_placeholders():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert "{{title_a}}" in t and "{{summary_a}}" in t
    assert "{{title_b}}" in t and "{{summary_b}}" in t


def test_story_link_confirm_prompt_has_output_schema():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert '"same_story"' in t
    assert '"reason"' in t


def test_story_link_confirm_prompt_forbids_naming_entities():
    t = load_prompt("src/prompts/story_link_confirm.md")
    assert "编造" in t or "不要" in t
