import pytest

from src.pipeline.release_importance import tier

# (scale, refactor, new_concept, bugfix_only) -> expected tier
# 穷举全部 16 种组合, 由 tier() 的判定逻辑手算得出 (spec §判定设计)
CASES = [
    ((False, False, False, False), 1),
    ((False, False, False, True), 1),
    ((False, False, True, False), 2),
    ((False, False, True, True), 2),
    ((False, True, False, False), 2),
    ((False, True, False, True), 2),
    ((False, True, True, False), 2),
    ((False, True, True, True), 2),
    ((True, False, False, False), 2),
    ((True, False, False, True), 1),
    ((True, False, True, False), 3),
    ((True, False, True, True), 3),
    ((True, True, False, False), 3),
    ((True, True, False, True), 3),
    ((True, True, True, False), 3),
    ((True, True, True, True), 3),
]


@pytest.mark.parametrize("dims,expected", CASES)
def test_tier_all_16_combinations(dims, expected):
    scale, refactor, new_concept, bugfix_only = dims
    assert tier(scale, refactor, new_concept, bugfix_only) == expected


def test_tier_refactor_or_new_concept_dominates_scale():
    # refactor/new_concept 命中时, scale 决定是 2 还是 3, bugfix_only 被忽略
    assert tier(scale=False, refactor=True, new_concept=False, bugfix_only=True) == 2
    assert tier(scale=True, refactor=True, new_concept=False, bugfix_only=True) == 3


def test_tier_never_returns_zero():
    # tier 0 (空 body) 是调用方短路判定, 不经过 tier(), 所以 tier() 本身最低返回 1
    for dims, _ in CASES:
        assert tier(*dims) >= 1
