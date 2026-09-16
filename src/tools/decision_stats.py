"""按来源统计你在 Telegram 上的保留率(只读)。

2026-09-15 用户判断: "只有 Hugging Face Paper 稳定, 按规则抓来的大部分不合适"。
日志只能量"解读有没有依据", 量不出"这条值不值得发"——后者唯一的依据是审阅时
点的保留/丢弃。决策存在 Cloudflare KV, 只有 item_id 且 7 天 TTL, 来源要靠
pending_reviews 关联, 而那份库只在 Actions 缓存里, 所以这个工具跑在手动 workflow 上。
"""

from __future__ import annotations

import asyncio
import os
import sys

from src.adapters.decisions.worker import WorkerDecisionStore
from src.core.config import load_delivery_config
from src.core.types import publisher_key
from src.state.db import Database


def keep_rate_table(rows: list[dict], decisions: dict[str, str]) -> list[dict]:
    """[{publisher, pushed, decided, keep, drop, keep_rate}], 保留率低的在前。

    X 条目按账号分组(publisher_key), 不然几十条 X 挤在一个 x_list 里看不出是谁。
    一条都没决策的来源 keep_rate 为 None——0% 和"没审过"必须分开, 否则会把
    从没推给你看过的来源当成你不想要的砍掉。
    """
    agg: dict[str, dict] = {}
    for r in rows:
        key = publisher_key(r["link"], r["source"])
        a = agg.setdefault(key, {"publisher": key, "pushed": 0, "decided": 0, "keep": 0, "drop": 0})
        a["pushed"] += 1
        action = decisions.get(r["item_id"])
        if action in ("keep", "drop"):
            a["decided"] += 1
            a[action] += 1
    out = []
    for a in agg.values():
        a["keep_rate"] = (a["keep"] / a["decided"]) if a["decided"] else None
        out.append(a)
    # 未决策的排最后: 它们不是"差", 只是没数据
    out.sort(key=lambda a: (a["keep_rate"] is None, a["keep_rate"], -a["pushed"]))
    return out


def render(table: list[dict]) -> str:
    lines = [
        "| 来源/账号 | 推送 | 已决策 | 保留 | 丢弃 | 保留率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for a in table:
        rate = "—" if a["keep_rate"] is None else f"{a['keep_rate'] * 100:.0f}%"
        lines.append(
            f"| {a['publisher']} | {a['pushed']} | {a['decided']} "
            f"| {a['keep']} | {a['drop']} | {rate} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    db_path = (argv or sys.argv[1:] or ["data/state.db"])[0]
    dcfg = load_delivery_config("config/delivery.yaml")
    secret = os.environ.get("DECISIONS_API_SECRET", "")
    if not (dcfg.decisions_api.url and secret):
        print("no decisions API configured (DECISIONS_API_SECRET missing)", file=sys.stderr)
        return 2
    store = WorkerDecisionStore(dcfg.decisions_api.url, secret)
    db = Database(db_path)

    async def _run():
        rows = await db.get_all_pending_reviews()
        decisions = await store.fetch()
        return rows, decisions

    rows, decisions = asyncio.run(_run())
    table = keep_rate_table(rows, decisions)
    print(f"推送条目 {len(rows)} 条, 拿到决策 {len(decisions)} 条(KV 只留 7 天)\n")
    print(render(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
