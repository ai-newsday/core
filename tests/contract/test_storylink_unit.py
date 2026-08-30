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
