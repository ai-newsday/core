"""验收用: 调 HF daily_papers API 取 N 天数据, 按 upvotes 排序, 每天 top K。
papers.cool 作为补充(无 upvote 信号, 不参与 ranking, 只展示 cool comment hint)。"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

DAYS = 5
TOP_PER_DAY = 2


async def _fetch_day(client: httpx.AsyncClient, date_str: str) -> list[dict]:
    url = f"https://huggingface.co/api/daily_papers?date={date_str}"
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.json() or []


async def main():
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=d)).isoformat() for d in range(DAYS)]
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_fetch_day(client, d) for d in days), return_exceptions=True
        )
    print(f"# 最近 {DAYS} 天 hf-papers 热门论文 (按 upvotes 排序, 每天 top {TOP_PER_DAY})\n")
    for day, rows in zip(days, results):
        if isinstance(rows, Exception):
            print(f"\n## {day}  FAIL: {rows}")
            continue
        if not rows:
            print(f"\n## {day}  (无)")
            continue
        # 解析 + 排序
        items = []
        for row in rows:
            p = row.get("paper", {})
            items.append(
                {
                    "id": p.get("id"),
                    "title": p.get("title", "")[:120],
                    "upvotes": p.get("upvotes", 0),
                    "comments": p.get("numComments", 0),
                    "stars": p.get("githubStars"),
                    "repo": p.get("githubRepo"),
                    "ai_kw": p.get("ai_keywords") or [],
                    "ai_sum": (p.get("ai_summary") or "")[:200],
                }
            )
        items.sort(key=lambda x: (-x["upvotes"], -x["comments"]))
        print(f"\n## {day}  共 {len(items)} 篇, 取 top {TOP_PER_DAY}\n")
        for i, it in enumerate(items[:TOP_PER_DAY], 1):
            stars = f" ⭐{it['stars']}" if it["stars"] else ""
            kw = " ｜ " + " · ".join(it["ai_kw"][:3]) if it["ai_kw"] else ""
            print(f"### {i}. [👍 {it['upvotes']} ｜ 💬 {it['comments']}{stars}] {it['title']}")
            print(f"- https://huggingface.co/papers/{it['id']}{kw}")
            if it["repo"]:
                print(f"- code: https://github.com/{it['repo']}")
            if it["ai_sum"]:
                print(f"- {it['ai_sum']}")
            print()
        # 列尾巴若干, 给个全景
        if len(items) > TOP_PER_DAY:
            tail = items[TOP_PER_DAY : TOP_PER_DAY + 8]
            print(f"_后续 {len(items) - TOP_PER_DAY} 篇 (top 排序前 8 节选)：_")
            for it in tail:
                print(
                    f"  - [👍 {it['upvotes']}] {it['title']}  https://huggingface.co/papers/{it['id']}"
                )


if __name__ == "__main__":
    asyncio.run(main())
