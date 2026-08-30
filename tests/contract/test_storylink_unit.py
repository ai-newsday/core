from datetime import datetime, timezone

from src.core.types import Genre, Publisher, ScoredItem
from src.pipeline.storylink import extract_entity_tokens, find_candidate_pairs

DEFAULT_PATTERN = r"\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b"

DAY1 = datetime(2026, 8, 27, 9, tzinfo=timezone.utc)
DAY1_LATE = datetime(2026, 8, 27, 20, tzinfo=timezone.utc)
DAY2 = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)


def _item(link, title_en, published_at, raw_summary=None, score=80):
    return ScoredItem(
        title_en=title_en,
        link=link,
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=published_at,
        raw_summary=raw_summary,
        cluster_id="evt-1",
        score=score,
        score_breakdown={"机构影响力": float(score)},
    )


def test_extract_entity_tokens_hyphenated_version():
    assert extract_entity_tokens("GLM-5.3 released today", DEFAULT_PATTERN) == {"GLM-5.3"}


def test_extract_entity_tokens_attached_version():
    assert extract_entity_tokens("Upgrade to v0.28.0 now", DEFAULT_PATTERN) == {"V0.28.0"}


def test_extract_entity_tokens_spaced_version():
    assert extract_entity_tokens("Llama 4 is here", DEFAULT_PATTERN) == {"LLAMA-4"}


def test_extract_entity_tokens_normalizes_case_and_separator():
    a = extract_entity_tokens("glm 5.3 dropped", DEFAULT_PATTERN)
    b = extract_entity_tokens("GLM-5.3 dropped", DEFAULT_PATTERN)
    assert a == b == {"GLM-5.3"}


def test_extract_entity_tokens_multiple_hits():
    text = "GLM-5.3 now supported alongside Llama 4 in v0.28.0 of the toolkit"
    tokens = extract_entity_tokens(text, DEFAULT_PATTERN)
    assert tokens == {"GLM-5.3", "LLAMA-4", "V0.28.0"}


def test_extract_entity_tokens_no_hits_returns_empty_set():
    assert (
        extract_entity_tokens("A generic announcement with no version numbers", DEFAULT_PATTERN)
        == set()
    )


def test_extract_entity_tokens_empty_text():
    assert extract_entity_tokens("", DEFAULT_PATTERN) == set()


def test_find_candidate_pairs_same_day_overlapping_tokens():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
    ]
    pairs = find_candidate_pairs(items, DEFAULT_PATTERN)
    assert pairs == [(0, 1)]


def test_find_candidate_pairs_different_days_excluded():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY2),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_no_token_overlap_excluded():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Unrelated announcement with Llama 4", DAY1_LATE),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_no_tokens_at_all_excluded():
    items = [
        _item("https://a/1", "Generic news with no version numbers", DAY1),
        _item("https://a/2", "Another generic announcement here", DAY1_LATE),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == []


def test_find_candidate_pairs_uses_raw_summary_too():
    items = [
        _item(
            "https://a/1", "Company A ships a new model", DAY1, raw_summary="It's called GLM-5.3."
        ),
        _item(
            "https://a/2", "Platform B blog post", DAY1_LATE, raw_summary="Now supporting GLM-5.3."
        ),
    ]
    assert find_candidate_pairs(items, DEFAULT_PATTERN) == [(0, 1)]


def test_find_candidate_pairs_three_items_multiple_pairs():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "Platform C also supports GLM-5.3", DAY1_LATE),
    ]
    pairs = find_candidate_pairs(items, DEFAULT_PATTERN)
    assert set(pairs) == {(0, 1), (0, 2), (1, 2)}


import json
import logging
import uuid

from src.core.types import RunContext, StoryLinkConfig
from src.pipeline.storylink import build_confirm_prompt, confirm_pair, link_stories
from tests.fakes import FailingLLMProvider, FakeLLMProvider

CFG = StoryLinkConfig()
TEMPLATE = "A: {{title_a}} / {{summary_a}}\nB: {{title_b}} / {{summary_b}}"


def _ctx(now=DAY1):
    return RunContext(run_id=str(uuid.uuid4()), now=now, logger=logging.getLogger("test"))


def test_build_confirm_prompt_substitutes_both_items():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1, raw_summary="Sum A")
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE, raw_summary="Sum B")
    out = build_confirm_prompt(a, b, TEMPLATE, CFG)
    assert "Company A releases GLM-5.3" in out and "Sum A" in out
    assert "Platform B supports GLM-5.3" in out and "Sum B" in out


def test_build_confirm_prompt_truncates_long_summary():
    a = _item("https://a/1", "T", DAY1, raw_summary="X" * 1000)
    b = _item("https://a/2", "T2", DAY1_LATE, raw_summary="short")
    cfg = StoryLinkConfig(summary_max_chars=50)
    out = build_confirm_prompt(a, b, TEMPLATE, cfg)
    assert "X" * 51 not in out


def test_confirm_pair_true():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": json.dumps({"same_story": True, "reason": "same model"})})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is True


def test_confirm_pair_false():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Unrelated GLM-5.3 retrospective", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": json.dumps({"same_story": False, "reason": "different"})})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is False


def test_confirm_pair_fail_closed_on_llm_error():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    assert confirm_pair(a, b, TEMPLATE, FailingLLMProvider(), CFG) is False


def test_confirm_pair_fail_closed_on_bad_json():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)
    llm = FakeLLMProvider({"Company A": "not json"})
    assert confirm_pair(a, b, TEMPLATE, llm, CFG) is False


def test_confirm_pair_fail_closed_on_non_bool_same_story():
    a = _item("https://a/1", "Company A releases GLM-5.3", DAY1)
    b = _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE)

    llm_string_true = FakeLLMProvider(
        {"Company A": json.dumps({"same_story": "true", "reason": "x"})}
    )
    assert confirm_pair(a, b, TEMPLATE, llm_string_true, CFG) is False

    llm_int_1 = FakeLLMProvider({"Company A": json.dumps({"same_story": 1, "reason": "x"})})
    assert confirm_pair(a, b, TEMPLATE, llm_int_1, CFG) is False


def test_link_stories_disabled_returns_items_unchanged():
    items = [
        _item("https://a/1", "GLM-5.3", DAY1),
        _item("https://a/2", "GLM-5.3 support", DAY1_LATE),
    ]
    cfg = StoryLinkConfig(enabled=False)
    out = link_stories(items, FailingLLMProvider(), cfg, _ctx())
    assert all(it.story_id is None for it in out)
    assert len(out) == 2


def test_link_stories_empty_input():
    assert link_stories([], FailingLLMProvider(), CFG, _ctx()) == []


def test_link_stories_merges_confirmed_pair():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "Unrelated news, no tokens here", DAY1),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    by_link = {it.link: it for it in out}
    assert by_link["https://a/1"].story_id is not None
    assert by_link["https://a/1"].story_id == by_link["https://a/2"].story_id
    assert by_link["https://a/3"].story_id is None
    assert len(out) == 3


def test_link_stories_story_id_format():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B now supports GLM-5.3", DAY1_LATE),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    assert out[0].story_id == "story-2026-08-27-001"


def test_link_stories_transitive_grouping():
    """A-B confirmed, B-C confirmed (but A-C never checked / no direct token overlap)
    -> all three in one connected component via union-find."""
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1, raw_summary="GLM-5.3 only"),
        _item("https://a/2", "Platform B supports GLM-5.3 and Llama 4", DAY1_LATE),
        _item("https://a/3", "Platform C supports Llama 4", DAY1_LATE, raw_summary="Llama 4 only"),
    ]
    llm = FakeLLMProvider(
        {
            "https://a/1": "",  # placeholder, overwritten below by substring match on titles
        }
    )
    # FakeLLMProvider matches by substring of the *prompt*; use title text instead
    llm = FakeLLMProvider(
        {
            "Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"}),
            "Platform C supports Llama 4": json.dumps({"same_story": True, "reason": "x"}),
        }
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    ids = {it.link: it.story_id for it in out}
    assert ids["https://a/1"] == ids["https://a/2"] == ids["https://a/3"]
    assert ids["https://a/1"] is not None


def test_link_stories_no_candidates_all_none():
    items = [
        _item("https://a/1", "Generic news one", DAY1),
        _item("https://a/2", "Generic news two", DAY1_LATE),
    ]
    out = link_stories(items, FailingLLMProvider(), CFG, _ctx(now=DAY1))
    assert all(it.story_id is None for it in out)


def test_link_stories_preserves_order_and_count():
    items = [
        _item("https://a/1", "Company A releases GLM-5.3", DAY1),
        _item("https://a/2", "Platform B supports GLM-5.3", DAY1_LATE),
        _item("https://a/3", "z-topic unrelated", DAY2),
    ]
    llm = FakeLLMProvider(
        {"Company A releases GLM-5.3": json.dumps({"same_story": True, "reason": "x"})}
    )
    out = link_stories(items, llm, CFG, _ctx(now=DAY1))
    assert [it.link for it in out] == ["https://a/1", "https://a/2", "https://a/3"]
