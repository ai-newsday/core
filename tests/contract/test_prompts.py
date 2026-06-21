import pytest

from src.core.prompts import load_prompt


def test_load_prompt_reads_file(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("hello {{title_en}}", encoding="utf-8")
    assert load_prompt(str(p)) == "hello {{title_en}}"


def test_load_prompt_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(str(tmp_path / "nope.md"))


def test_repo_prompts_exist_and_have_placeholders():
    item = load_prompt("src/prompts/interpret_item.md")
    assert "{{title_en}}" in item and "{{raw_summary}}" in item
    assert "{{link}}" in item and "{{related_links}}" in item
    daily = load_prompt("src/prompts/daily_take.md")
    assert "{{items}}" in daily


def test_interpret_prompt_uses_body_schema():
    from src.core.prompts import load_prompt

    t = load_prompt("src/prompts/interpret_item.md")
    assert "`body`" in t
    assert '"body"' in t
    assert "takeaway" not in t
    assert "hot_take" not in t
