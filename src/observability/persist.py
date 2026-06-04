"""run 产物落盘: 每次跑流水线时把各层产出写到 data/runs/<run_id>/。
便于事后核对、做信号回溯, 取代 PRD 提的 SQLite SSOT (P1 接 SQLite 时换底)。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from pydantic import BaseModel


_RUNS_ROOT = Path("data/runs")


def run_dir(run_id: str) -> Path:
    """data/runs/<run_id>/, 创建即返回。"""
    p = _RUNS_ROOT / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj


def dump_jsonl(items: list[Any], path: Path) -> None:
    """每行一个 JSON 对象 (BaseModel 自动序列化)。"""
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(_to_jsonable(it), ensure_ascii=False) + "\n")


def dump_json(obj: Any, path: Path) -> None:
    """单个对象 JSON; 含 indent 便于人读。"""
    with path.open("w", encoding="utf-8") as f:
        json.dump(_to_jsonable(obj), f, ensure_ascii=False, indent=2)
