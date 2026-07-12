from pathlib import Path

from src.pipeline.metrics import (
    compute_funnel,
    compute_per_genre,
    compute_per_source_top10,
    compute_rates,
    load_fallback_titles,
    load_trend_7d,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "metrics_run_dir"
FIXTURE_HISTORY = Path(__file__).parent.parent / "fixtures" / "metrics_history"


def test_compute_funnel_from_fixture_run_dir():
    f = compute_funnel(FIXTURE)
    assert f == {
        "candidates": 5,
        "after_dedup": 4,
        "after_score_quota": 2,
        "interpreted_ok": 1,
        "interpreted_fallback": 1,
        "review_eligible": 2,
        "posted": 1,
    }


def test_compute_funnel_empty_run_dir_returns_zeros(tmp_path):
    # Empty run_dir → all zeros, no crash
    f = compute_funnel(tmp_path)
    assert f == {
        "candidates": 0,
        "after_dedup": 0,
        "after_score_quota": 0,
        "interpreted_ok": 0,
        "interpreted_fallback": 0,
        "review_eligible": 0,
        "posted": 0,
    }


def test_compute_rates_normal():
    funnel = {
        "candidates": 87,
        "after_dedup": 68,
        "after_score_quota": 24,
        "interpreted_ok": 21,
        "interpreted_fallback": 3,
        "review_eligible": 20,
        "posted": 12,
    }
    r = compute_rates(funnel)
    assert r["fallback_rate"] == 3 / 24
    assert r["dedup_reduction"] == 1 - 68 / 87
    assert r["quota_reduction"] == 1 - 24 / 68
    assert r["interpret_fail_rate"] == 3 / 24
    assert r["keep_rate"] == 12 / 20


def test_compute_rates_division_by_zero_returns_zero():
    r = compute_rates(
        {
            "candidates": 0,
            "after_dedup": 0,
            "after_score_quota": 0,
            "interpreted_ok": 0,
            "interpreted_fallback": 0,
            "review_eligible": 0,
            "posted": 0,
        }
    )
    assert r == {
        "fallback_rate": 0.0,
        "dedup_reduction": 0.0,
        "quota_reduction": 0.0,
        "interpret_fail_rate": 0.0,
        "keep_rate": 0.0,
    }


def test_compute_per_genre_from_fixture():
    # Fixture has 3 papers + 1 news + 1 release in 01_collected; 1 news kept in 05
    g = compute_per_genre(FIXTURE)
    assert g["paper"]["candidates"] == 3
    assert g["paper"]["posted"] == 0
    assert g["paper"]["noise_ratio"] == 1.0
    assert g["news"]["candidates"] == 1
    assert g["news"]["posted"] == 1
    assert g["news"]["noise_ratio"] == 0.0
    assert g["release"]["candidates"] == 1
    assert g["release"]["posted"] == 0
    assert g["release"]["noise_ratio"] == 1.0


def test_compute_per_source_top10_sorted_by_yield():
    top = compute_per_source_top10(FIXTURE)
    assert top[0]["name"] == "hf-papers"
    assert top[0]["yield"] == 3
    assert top[0]["kept"] == 0
    assert top[0]["noise_ratio"] == 1.0
    assert top[1]["yield"] in (1,)  # tie between the-decoder and github-releases
    assert len(top) == 3


def test_load_fallback_titles_limits():
    titles = load_fallback_titles(FIXTURE, limit=5)
    assert titles == ["Paper A"]  # only 1 fallback in fixture; limit doesn't force padding
    assert load_fallback_titles(FIXTURE, limit=0) == []


def test_load_trend_7d_fills_missing_with_none():
    trend = load_trend_7d(FIXTURE_HISTORY, "2026-07-01")
    assert trend["dates"] == [
        "2026-06-25",
        "2026-06-26",
        "2026-06-27",
        "2026-06-28",
        "2026-06-29",
        "2026-06-30",
        "2026-07-01",
    ]
    # 2026-06-25/26/27/30 missing → None
    assert trend["fallback_rate"] == [None, None, None, 0.1, 0.167, None, 0.125]
    # eligible_rate = posted / candidates
    assert trend["eligible_rate"][3] == 10 / 80
    assert trend["eligible_rate"][4] == 13 / 90
    assert trend["eligible_rate"][6] == 12 / 87
    assert trend["eligible_rate"][0] is None
