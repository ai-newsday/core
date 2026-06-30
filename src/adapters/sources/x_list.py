from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.types import RawItem, RunContext, SourceSpec

logger = logging.getLogger(__name__)

# 对齐 src/pipeline/interpret.py:59 _SENT_ENDS
_SENT_ENDS = "。！？!?；;."


def _tweet_title(text: str, n: int = 140) -> str:
    """推文首行取标题, 超长按句末 → 词界 → 硬切+省略号 三档降级。

    n 默认 140 ≈ X 推文上限 280 的一半, 给后续 interpret 层 LLM 翻译/精炼留余地;
    interpret/review 层另有 title_max_chars=64 二次夹紧, 这里不参与最终长度。
    """
    first_line = text.split("\n", 1)[0].strip()
    if len(first_line) <= n:
        return first_line
    window = first_line[:n]
    cut = max((window.rfind(ch) for ch in _SENT_ENDS), default=-1)
    if cut >= 0:
        return window[: cut + 1]
    ws = window.rfind(" ")
    if ws > n // 2:
        return window[:ws] + "…"
    return first_line[: n - 1] + "…"


class XListAdapter:
    """Filesystem adapter: reads X (Twitter) list-timeline tweets from
    data/x/<date>.ndjson, routes by source.url == 'xlist:<list_id>'.

    PR-1: read-only, no LLM, no network. data_dir is constructor-injectable
    for tests; production singleton uses default ./data/x.
    """

    def __init__(self, data_dir: Path | str = "data/x") -> None:
        self._data_dir = Path(data_dir)

    async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
        if not self._data_dir.is_dir():
            return []
        list_id = _parse_list_id(source.url)
        if list_id is None:
            logger.warning("x_list source %s has invalid url %r", source.name, source.url)
            return []

        items: list[RawItem] = []
        for path in _candidate_files(self._data_dir, ctx.now):
            for row in _iter_ndjson(path):
                if row.get("list_id") != list_id:
                    continue
                item = _row_to_raw_item(row, source)
                if item is not None:
                    items.append(item)
        return items


def _parse_list_id(url: str) -> str | None:
    if not url.startswith("xlist:"):
        return None
    lid = url[len("xlist:") :].strip()
    return lid or None


def _candidate_files(data_dir: Path, now: datetime) -> list[Path]:
    """读 today-UTC + yesterday-UTC 两文件 (finalize 跑在 01:00 UTC, 这两天合并刚覆盖晨报关心的 24h 窗)。"""
    today = now.astimezone(timezone.utc).date()
    yday = today.fromordinal(today.toordinal() - 1)
    return [
        data_dir / f"{yday.isoformat()}.ndjson",
        data_dir / f"{today.isoformat()}.ndjson",
    ]


def _iter_ndjson(path: Path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("x_list malformed ndjson at %s:%d", path, lineno)


def _row_to_raw_item(row: dict, source: SourceSpec) -> RawItem | None:
    try:
        text = str(row["text"])
        permalink = str(row["permalink"])
        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        author = str(row.get("author_handle") or "")
        quoted = row.get("quoted_text")
        body_parts = [f"@{author}:\n{text}"] if author else [text]
        if quoted:
            qa = row.get("quoted_author_handle") or ""
            body_parts.append(f"\n\n> 引用 @{qa}: {quoted}")
        body = "".join(body_parts)
        signals = {
            "x_favorite": row.get("favorite_count") or 0,
            "x_retweet": row.get("retweet_count") or 0,
            "x_quote": row.get("quote_count") or 0,
            "x_reply": row.get("reply_count") or 0,
        }
        return RawItem(
            title_en=_tweet_title(text),
            link=permalink,
            source=source.name,
            genre=source.genre,
            publisher=source.publisher,
            published_at=created,
            raw_summary=body,
            signals=signals,
            fetched_via="native",
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("x_list bad row %r: %s", row.get("tweet_id"), e)
        return None
