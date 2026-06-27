from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.github_releases import GithubReleasesAdapter
from src.adapters.sources.github_trending import GithubTrendingAdapter
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.rss import RSSAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
    "hn": HNAdapter(),
    "github_releases": GithubReleasesAdapter(),
    "github_trending": GithubTrendingAdapter(),
}
