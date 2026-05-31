import yaml
from src.core.types import SourceSpec


def test_sources_yaml_all_valid_specs():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]          # raises if any entry invalid
    assert len(specs) >= 30


def test_working_set_has_primaries_and_no_duplicates_urls():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]
    working = [s for s in specs if s.status == "working"]
    names = {s.name for s in working}
    assert {"hf-papers", "hf-models"} <= names       # primary sources enabled
    urls = [s.url for s in specs]
    assert len(urls) == len(set(urls))               # no dup URLs across registry
