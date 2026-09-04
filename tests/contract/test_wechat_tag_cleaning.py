"""公众号话题标签清洗 (spec 2026-08-31 §4)。

微信的话题标签**遇到符号即截断**——`#GLM-5.3-Flash` 在读者那边显示成 `#GLM`,
`#AI Tutor` 在空格处断成 `#AI`。prompt 层已经要求模型只用字母/汉字, 但 LLM 必然
违反(2026-09-04 真实产出里仍有 `#GPT-6Astra`), 所以代码层要兜底:与其让它在读者
那边断得莫名其妙, 不如我们先断好。
"""

from src.pipeline.publish import clean_wechat_tag, clean_wechat_tags


def test_cuts_at_hyphen():
    assert clean_wechat_tag("#GLM-5.3-Flash") == "#GLM"


def test_cuts_at_digit_not_after_it():
    """用户 2026-09-02 明确要求: 不要 `Qwen3`, 直接 `Qwen`。"""
    assert clean_wechat_tag("#Qwen3.8-Flash-Next") == "#Qwen"


def test_cuts_at_digit_inside_a_word():
    """2026-09-04 真实产出: 微信会把它断成 `#GPT`, 不如我们先断。"""
    assert clean_wechat_tag("#GPT-6Astra") == "#GPT"


def test_cuts_at_space():
    """空格同样截断 —— 2026-09-03 手工修过 `#AI Tutor`, 当时没进流水线。"""
    assert clean_wechat_tag("#AI Tutor") == "#AI"


def test_pure_chinese_is_untouched():
    assert clean_wechat_tag("#具身智能") == "#具身智能"


def test_letters_then_chinese_is_untouched():
    """`AI代理` 全是字母与汉字, 中间没有符号或数字, 微信不会断。"""
    assert clean_wechat_tag("#AI代理") == "#AI代理"


def test_leading_digit_falls_back_to_symbol_stripping_instead_of_emptying():
    """spec §4: 截断后不足 2 字符 -> 退回"仅去除符号", 避免 `#3D生成` 被清空。"""
    assert clean_wechat_tag("#3D生成") == "#3D生成"


def test_single_letter_prefix_also_falls_back():
    """`#K2-Horizon` 按规则截出 `K`(1 字符), 太短没有意义, 退回去符号形式。"""
    assert clean_wechat_tag("#K2-Horizon") == "#K2Horizon"


def test_tag_without_hash_still_works():
    assert clean_wechat_tag("GLM-5.3") == "#GLM"


def test_blank_tag_yields_nothing():
    assert clean_wechat_tag("#") == ""
    assert clean_wechat_tag("") == ""


def test_list_drops_empties_and_deduplicates():
    """两个不同标签清洗后可能撞成同一个(`#Qwen3.8` 与 `#Qwen-Drive` 都成 `#Qwen`),
    重复的话题标签对读者是纯噪音。"""
    out = clean_wechat_tags(["#Qwen3.8-Flash", "#Qwen-Drive", "#自动驾驶", "#"])
    assert out == ["#Qwen", "#自动驾驶"]


def test_list_preserves_order():
    assert clean_wechat_tags(["#具身智能", "#GLM-5.3", "#开源"]) == ["#具身智能", "#GLM", "#开源"]
