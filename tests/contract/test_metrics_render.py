from src.pipeline.metrics_render import render_png

SAMPLE_DATA = {
    "date": "2026-07-01",
    "funnel": {
        "candidates": 87,
        "after_dedup": 68,
        "after_score_quota": 24,
        "interpreted_ok": 21,
        "interpreted_fallback": 3,
        "review_eligible": 20,
        "posted": 12,
    },
    "rates": {
        "fallback_rate": 0.125,
        "dedup_reduction": 0.218,
        "quota_reduction": 0.647,
        "interpret_fail_rate": 0.125,
        "keep_rate": 0.6,
    },
    "per_genre": {},
    "per_source_top10": [],
    "samples": {"fallback_titles": []},
    "trend_7d": {
        "dates": [
            "2026-06-25",
            "2026-06-26",
            "2026-06-27",
            "2026-06-28",
            "2026-06-29",
            "2026-06-30",
            "2026-07-01",
        ],
        "fallback_rate": [0.15, 0.18, 0.11, 0.14, 0.20, 0.16, 0.125],
        "eligible_rate": [0.12, 0.10, 0.15, 0.13, 0.08, 0.14, 0.138],
    },
}


def test_render_png_writes_valid_png_at_expected_size(tmp_path):
    out = tmp_path / "test.png"
    render_png(SAMPLE_DATA, out)
    assert out.is_file()
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert 5_000 < len(data) < 200_000


def test_render_png_handles_missing_trend_gracefully(tmp_path):
    d = {
        **SAMPLE_DATA,
        "trend_7d": {
            "dates": SAMPLE_DATA["trend_7d"]["dates"],
            "fallback_rate": [None] * 7,
            "eligible_rate": [None] * 7,
        },
    }
    out = tmp_path / "test2.png"
    render_png(d, out)
    assert out.is_file()
