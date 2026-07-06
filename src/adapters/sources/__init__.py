import os

from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.github_releases import GithubReleasesAdapter
from src.adapters.sources.github_trending import GithubTrendingAdapter
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.rss import RSSAdapter
from src.adapters.sources.x_list import XListAdapter

# X_LIST_DATA_DIR override: prod cron clones ai-newsday/x-signals into .cache/x-signals
# and points here; local dev falls back to ./data/x. Set in .github/workflows/collect.yml.
_X_LIST_DATA_DIR = os.environ.get("X_LIST_DATA_DIR", "data/x")

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
    "hn": HNAdapter(),
    "github_releases": GithubReleasesAdapter(),
    "github_trending": GithubTrendingAdapter(),
    "x_list": XListAdapter(data_dir=_X_LIST_DATA_DIR),
}
