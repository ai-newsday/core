import yaml

from src.core.types import SourceSpec


def test_sources_yaml_all_valid_specs():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]  # raises if any entry invalid
    assert len(specs) >= 30


def test_working_set_has_primaries_and_no_duplicates_urls():
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = [SourceSpec(**r) for r in rows]
    working = [s for s in specs if s.status == "working"]
    names = {s.name for s in working}
    assert {"hf-papers", "hf-models"} <= names  # primary sources enabled
    urls = [s.url for s in specs]
    assert len(urls) == len(set(urls))  # no dup URLs across registry


def test_langchain_gh_filters_to_main_package_tags():
    # 2026-07-25 实测: langchain monorepo 每个子包(-openai/-fireworks/-core...)独立
    # 打 tag, 真正的主包 langchain== 反而最少见; 没有过滤会被子包噪声淹没。
    rows = yaml.safe_load(open("config/sources.yaml"))
    specs = {r["name"]: SourceSpec(**r) for r in rows}
    lc = specs["langchain-gh"]
    assert lc.tag_pattern is not None
    import re

    pat = re.compile(lc.tag_pattern)
    assert pat.search("langchain==1.3.14")
    assert not pat.search("langchain-fireworks==1.5.1")
    assert not pat.search("langchain-core==1.5.1")
