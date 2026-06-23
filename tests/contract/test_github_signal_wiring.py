from datetime import datetime, timezone

import yaml

from src.core.types import Genre, Publisher, RawItem, SourceSpec
from src.pipeline.enrich import _has_popularity


def test_sourcespec_accepts_github_adapters():
    for ad in ("github_releases", "github_trending"):
        s = SourceSpec(
            name="x",
            url="https://api.github.com/x",
            genre=Genre.announcement,
            publisher=Publisher.company,
            adapter=ad,
        )
        assert s.adapter == ad


def test_github_stars_counts_as_popularity():
    it = RawItem(
        title_en="t",
        link="l",
        source="s",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=datetime(2026, 6, 23, tzinfo=timezone.utc),
        signals={"github_stars": 1200},
    )
    assert _has_popularity(it) is True


def test_scoring_yaml_has_github_stars_weight():
    cfg = yaml.safe_load(open("config/scoring.yaml"))
    assert cfg["popularity_weights"]["github_stars"] == 0.3
