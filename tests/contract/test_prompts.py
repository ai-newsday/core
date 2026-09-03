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


def test_interpret_prompt_has_entity_confident_field():
    t = load_prompt("src/prompts/interpret_item.md")
    assert "entity_confident" in t
    assert '"entity_confident"' in t
    assert "宁可标 false" in t


def test_interpret_prompt_forbids_dollar_sign_in_body():
    """2026-09-01 实测: 正文里的 `$5090`/`$6.9K`/`$4.4K` 被 Markdown 当成成对的
    行内数学定界符, 把文字渲染成 `**性** **能** **逼** **近**`。AI 新闻里价格/
    成本/融资很常见, 会反复出现。"""
    t = load_prompt("src/prompts/interpret_item.md")
    assert "美元" in t
    assert "$" in t  # 规则里要举出反例才说得清


def test_interpret_prompt_bans_boilerplate_closers():
    """2026-09-01 实测: 7 条里 2 条以近乎相同的「对从业者而言, 这意味着…」收尾。"""
    t = load_prompt("src/prompts/interpret_item.md")
    assert "对从业者而言" in t
    assert "这意味着" in t


def test_interpret_prompt_requires_chinese_body():
    """2026-09-01 实测: 正文里混着未翻译的 `practitioners`。"""
    t = load_prompt("src/prompts/interpret_item.md")
    assert "专有名词" in t or "术语" in t


def test_daily_take_prompt_targets_three_events_in_title():
    """2026-09-03 用户要求: 标题目标 3 个事件(原为默认 2 个)。"""
    t = load_prompt("src/prompts/daily_take.md")
    assert "3 个事件" in t


def test_daily_take_prompt_lets_title_degrade_when_too_long():
    """字数是硬约束, 事件数量必须服从字数, 不能反过来——不然"3 个事件"会
    直接顶替"≤64 字"变成新的失败模式(3 个事件比 2 个更难塞进 64 字)。"""
    t = load_prompt("src/prompts/daily_take.md")
    assert "砍成" in t or "少放" in t or "少写" in t


def test_daily_take_prompt_targets_four_to_five_events_in_digest():
    """2026-09-03 用户要求: 摘要目标 4-5 个事件(原为 2-3 段)。"""
    t = load_prompt("src/prompts/daily_take.md")
    assert "4-5 段" in t or "4-5 个" in t
