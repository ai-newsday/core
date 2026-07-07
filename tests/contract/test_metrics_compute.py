from pathlib import Path

from src.pipeline.metrics import compute_funnel, compute_rates

FIXTURE = Path(__file__).parent.parent / "fixtures" / "metrics_run_dir"


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
