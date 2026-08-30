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


def test_interpret_prompt_has_relevant_field():
    from src.core.prompts import load_prompt

    t = load_prompt("src/prompts/interpret_item.md")
    assert "relevant" in t
    assert '"relevant"' in t


def test_interpret_prompt_forbids_misattributing_hosting_platform():
    # 2026-07-25 实测: hf-papers/hf-models 的 raw_summary 只有摘要文本, 没有作者
    # 机构字段(HF daily_papers API 只给作者姓名), 但 LLM 常把"Hugging Face"(托管
    # 平台)写成论文发布方("Hugging Face 发布论文")。禁止这种误归因。
    t = load_prompt("src/prompts/interpret_item.md")
    assert "Hugging Face" in t and "托管" in t


def test_interpret_prompt_has_content_certain_field():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "content_certain" in t
    assert '"content_certain"' in t


def test_interpret_prompt_has_title_action_word_guard():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "动作词" in t
    assert "依据" in t


def test_interpret_prompt_has_paper_title_hook_rule():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "paper" in t
    assert "方法名" in t or "模型名" in t


def test_release_importance_prompt_exists_and_has_placeholders():
    t = load_prompt("src/prompts/release_importance.md")
    assert "{{title}}" in t and "{{body}}" in t


def test_release_importance_prompt_has_four_dimension_schema():
    t = load_prompt("src/prompts/release_importance.md")
    for key in ('"scale"', '"refactor"', '"new_concept"', '"bugfix_only"', '"reason"'):
        assert key in t
