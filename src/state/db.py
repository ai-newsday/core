from __future__ import annotations
import json
from datetime import datetime, timezone
import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tick   TEXT NOT NULL,
    ts     TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    notes  TEXT
);

CREATE TABLE IF NOT EXISTS pending_reviews (
    item_id    TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    link       TEXT NOT NULL,
    source     TEXT NOT NULL,
    title_en   TEXT NOT NULL,
    title_zh   TEXT,
    summary_zh TEXT,
    takeaway   TEXT,
    hot_take   TEXT,
    score      INTEGER,
    signals    TEXT,
    msg_id     INTEGER,
    status     TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT,
    date       TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    link    TEXT NOT NULL,
    source  TEXT NOT NULL,
    action  TEXT NOT NULL,
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_weights (
    source     TEXT PRIMARY KEY,
    weight     REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str = "data/state.db"):
        self._path = path

    async def init(self) -> None:
        """建表（幂等）。"""
        async with aiosqlite.connect(self._path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.executescript(_SCHEMA)
            await conn.commit()

    async def insert_run(self, run_id: str, tick: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO runs(run_id,tick,ts,status) VALUES(?,?,?,?)",
                (run_id, tick, ts, "running"))
            await conn.commit()

    async def get_run(self, run_id: str) -> dict | None:
        async with aiosqlite.connect(self._path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                    "SELECT * FROM runs WHERE run_id=?", (run_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def upsert_pending_review(
            self, *, item_id: str, run_id: str, link: str, source: str,
            title_en: str, title_zh: str | None, summary_zh: str | None,
            takeaway: str | None, hot_take: str | None,
            score: int, signals: dict, date: str) -> None:
        """INSERT OR IGNORE — 同一 item_id 今天已有记录则跳过。"""
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute("""
                INSERT OR IGNORE INTO pending_reviews
                (item_id,run_id,link,source,title_en,title_zh,summary_zh,
                 takeaway,hot_take,score,signals,date)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (item_id, run_id, link, source, title_en, title_zh,
                  summary_zh, takeaway, hot_take, score,
                  json.dumps(signals, ensure_ascii=False), date))
            await conn.commit()

    async def update_decision(self, item_id: str, action: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "UPDATE pending_reviews SET status=?,decided_at=? WHERE item_id=?",
                (action, ts, item_id))
            await conn.commit()

    async def update_msg_id(self, item_id: str, msg_id: int) -> None:
        async with aiosqlite.connect(self._path) as conn:
            await conn.execute(
                "UPDATE pending_reviews SET msg_id=? WHERE item_id=?",
                (msg_id, item_id))
            await conn.commit()

    async def get_pending_reviews_for_date(self, date: str) -> list[dict]:
        async with aiosqlite.connect(self._path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                    "SELECT * FROM pending_reviews WHERE date=? ORDER BY score DESC",
                    (date,)) as cur:
                rows = await cur.fetchall()
                result = []
                for row in rows:
                    d = dict(row)
                    d["signals"] = json.loads(d["signals"] or "{}")
                    result.append(d)
                return result

    async def get_decisions_dict(self, date: str) -> dict[str, str]:
        """返回 {link: action} 只含已明确决策（keep/drop）的条目。"""
        rows = await self.get_pending_reviews_for_date(date)
        return {r["link"]: r["status"]
                for r in rows if r["status"] in ("keep", "drop")}
