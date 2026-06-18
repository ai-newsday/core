from src.adapters.sources import ADAPTERS
from src.adapters.sources.hf_models import HFModelsAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hn import HNAdapter
from src.adapters.sources.reddit import RedditAdapter
from src.adapters.sources.rss import RSSAdapter


def test_adapters_map_covers_all_adapter_keys():
    assert set(ADAPTERS) == {"rss", "hf_papers", "hf_models", "hn", "reddit"}
    assert isinstance(ADAPTERS["rss"], RSSAdapter)
    assert isinstance(ADAPTERS["hf_papers"], HFPapersAdapter)
    assert isinstance(ADAPTERS["hf_models"], HFModelsAdapter)
    assert isinstance(ADAPTERS["hn"], HNAdapter)
    assert isinstance(ADAPTERS["reddit"], RedditAdapter)
