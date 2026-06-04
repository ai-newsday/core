"""一次性诊断: 拉 hf-papers + papers.cool 4 源, 按日期分组列出最近 5 天 paper。
绕过 24h 窗口过滤, 便于人工验收 paper 来源质量。"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import yaml
from src.adapters.sources import ADAPTERS
from src.core.types import SourceSpec, RunContext

SOURCES = ["hf-papers", "papers-cool-ai", "papers-cool-cl",
           "papers-cool-lg", "papers-cool-cv"]


async def _fetch(spec_d):
    spec = SourceSpec(**spec_d)
    ctx = RunContext(run_id="diag", now=datetime.now(timezone.utc),
                     logger=logging.getLogger("diag"))
    try:
        items = await asyncio.wait_for(
            ADAPTERS[spec.adapter].fetch(spec, ctx, 30), timeout=30)
        return spec.name, items, None
    except Exception as e:
        return spec.name, [], str(e)[:120]


async def main():
    cfg = yaml.safe_load(open("config/sources.yaml"))
    by_name = {s["name"]: s for s in cfg}
    res = await asyncio.gather(
        *(_fetch(by_name[n]) for n in SOURCES if n in by_name))
    by_day = defaultdict(list)
    for name, items, err in res:
        if err:
            print(f"[{name}] FAIL: {err}")
            continue
        for it in items:
            by_day[it.published_at.date().isoformat()].append(
                (name, it.title_en, it.link))
    today = datetime.now(timezone.utc).date()
    print(f"# 最近 5 天热门 paper (hf-papers ⭐ + papers.cool 📘)\n")
    for delta in range(0, 5):
        day = (today - timedelta(days=delta)).isoformat()
        items = by_day.get(day, [])
        if not items:
            print(f"\n## {day}  (无)")
            continue
        print(f"\n## {day}  共 {len(items)} 条")
        seen, uniq = set(), []
        for src, title, link in items:
            if link in seen:
                continue
            seen.add(link)
            uniq.append((src, title, link))
        uniq.sort(key=lambda x: (0 if x[0] == "hf-papers" else 1, x[1]))
        for src, title, link in uniq[:25]:
            tag = "⭐hf" if src == "hf-papers" else f"📘{src.split('-')[-1]}"
            print(f"- {tag}  **{title[:100]}**  {link}")
        if len(uniq) > 25:
            print(f"- _(还有 {len(uniq)-25} 条)_")


if __name__ == "__main__":
    asyncio.run(main())
