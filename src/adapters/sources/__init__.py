from src.adapters.sources.base import SourceAdapter
from src.adapters.sources.rss import RSSAdapter
from src.adapters.sources.hf_papers import HFPapersAdapter
from src.adapters.sources.hf_models import HFModelsAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    "rss": RSSAdapter(),
    "hf_papers": HFPapersAdapter(),
    "hf_models": HFModelsAdapter(),
}
