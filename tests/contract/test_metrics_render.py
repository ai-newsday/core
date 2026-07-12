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


from src.pipeline.metrics_render import render_caption, render_md  # noqa: E402

SAMPLE_DATA_FULL = {
    **SAMPLE_DATA,
    "per_genre": {
        "paper": {"candidates": 32, "posted": 1, "noise_ratio": 0.969},
        "news": {"candidates": 12, "posted": 3, "noise_ratio": 0.75},
    },
    "per_source_top10": [
        {"name": "hf-papers", "yield": 30, "kept": 1, "noise_ratio": 0.967},
    ],
    "samples": {"fallback_titles": ["Paper A", "Release B"]},
}


def test_render_md_has_front_matter_and_key_fields():
    md = render_md(SAMPLE_DATA_FULL)
    assert md.startswith("---\n")
    assert 'title: "Metrics 2026-07-01"' in md
    assert "type: metrics" in md
    assert "draft: false" in md
    assert "![funnel](./2026-07-01.png)" in md
    assert "87" in md and "12" in md and "12.5%" in md
    assert "paper" in md and "96.9%" in md
    assert "Paper A" in md
    assert "[原始 JSON](./2026-07-01.json)" in md


def test_render_caption_single_emoji_and_html_link():
    cap = render_caption(SAMPLE_DATA_FULL)
    assert cap.count("📊") == 1
    assert "候选 87" in cap
    assert "合格 12" in cap
    assert "fallback 3" in cap
    assert 'href="https://ai-newsday.github.io/core/metrics/2026-07-01/"' in cap
    for banned in ("⚠️", "🔊", "❌", "✅"):
        assert banned not in cap


def test_render_md_shows_fallback_breakdown():
    from src.pipeline.metrics_render import render_md

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {"ValueError": 10, "HTTPStatusError": 5}}
    md = render_md(data)
    assert "## fallback 分类" in md
    assert "ValueError" in md
    assert "10" in md
    assert "HTTPStatusError" in md
    assert "5" in md


def test_render_caption_shows_top_fail_when_breakdown_nonempty():
    from src.pipeline.metrics_render import render_caption

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {"ValueError": 10, "HTTPStatusError": 5}}
    cap = render_caption(data)
    assert "top fail: ValueError × 10" in cap


def test_render_caption_hides_top_fail_when_breakdown_empty():
    from src.pipeline.metrics_render import render_caption

    data = {**SAMPLE_DATA_FULL, "fallback_breakdown": {}}
    cap = render_caption(data)
    assert "top fail" not in cap
