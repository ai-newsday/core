import logging
from datetime import datetime, timezone

from src.core.registry import FALLBACK_SOURCES, load_registry
from src.core.types import RunContext


def _ctx():
    return RunContext(
        run_id="t", now=datetime.now(timezone.utc), logger=logging.getLogger("test.registry")
    )


def test_load_returns_only_working_sources():
    specs = load_registry("tests/golden/data/registry_min.yaml", _ctx())
    names = {s.name for s in specs}
    assert names == {"hf-papers", "openai"}  # 'some-blog' is manual -> excluded


def test_missing_file_falls_back_and_warns(caplog):
    with caplog.at_level(logging.INFO, logger="test.registry"):
        specs = load_registry("does/not/exist.yaml", _ctx())
    assert specs == FALLBACK_SOURCES
    assert any("registry_load_failed" in r.message for r in caplog.records)


def test_fallback_sources_are_all_working():
    assert FALLBACK_SOURCES
    assert all(s.status == "working" for s in FALLBACK_SOURCES)


_E = (
    "  genre: announcement\n  publisher: lab\n  adapter: rss\n"
    "  status: {status}\n  priority: {priority}\n"
)


def _row(name: str, status: str = "working", priority: int = 3) -> str:
    return f"- name: {name}\n  url: https://{name}/feed\n" + _E.format(
        status=status, priority=priority
    )


def test_load_merges_sources_d_directory(tmp_path):
    """主 registry + 同名 `.d/` 目录下的 *.yaml 全部 merge,实现源拆分维护。"""
    main = tmp_path / "sources.yaml"
    main.write_text(_row("a", "working", 2))
    extra_dir = tmp_path / "sources.d"
    extra_dir.mkdir()
    (extra_dir / "community.yaml").write_text(_row("b", "working", 3) + _row("c", "manual", 5))
    (extra_dir / "extra.yaml").write_text(_row("d", "working", 4))
    specs = load_registry(str(main), _ctx())
    names = {s.name for s in specs}
    assert names == {"a", "b", "d"}  # working only; c (manual) excluded


def test_load_works_without_sources_d_directory(tmp_path):
    """无 `.d/` 目录时,仅读主文件,不报错。"""
    main = tmp_path / "sources.yaml"
    main.write_text(_row("a", "working", 2))
    specs = load_registry(str(main), _ctx())
    assert [s.name for s in specs] == ["a"]


def test_load_source_priorities_merges_sources_d_directory(tmp_path):
    """priorities 同样需要看到 .d/ 下条目,避免打分时新源 priority 缺失。"""
    from src.core.registry import load_source_priorities

    main = tmp_path / "sources.yaml"
    main.write_text(_row("a", "working", 2))
    extra_dir = tmp_path / "sources.d"
    extra_dir.mkdir()
    (extra_dir / "community.yaml").write_text(_row("b", "manual", 5))
    prios = load_source_priorities(str(main))
    assert prios == {"a": 2, "b": 5}
