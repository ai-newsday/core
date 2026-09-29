import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import Evidence, Genre, InterpretedItem, Publisher
from src.notifiers import FakeNotifier
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


def _item(link: str, title: str, genre: Genre = Genre.news) -> InterpretedItem:
    return InterpretedItem(
        # RawItem fields
        title_en=title,
        link=link,
        source="test-source",
        genre=genre,
        publisher=Publisher.media,
        published_at=NOW,
        signals={},
        # NewsItem fields
        cluster_id=hashlib.sha256(link.encode()).hexdigest()[:16],
        related_links=[],
        # ScoredItem fields
        score=80,
        score_breakdown={"技术价值": 80.0},
        # InterpretedItem fields
        title=title,
        body="测试正文，一段顺读内容。",
        tags=["AI", "测试", "新闻"],
        evidence=[Evidence(claim="测试声明", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=True,
    )


def _iid(link: str) -> str:
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def test_finalize_merges_remote_decision(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "Keep me"), _item("https://x/2", "Drop me")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/2"): "drop"})
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        assert store.fetch_count == 1
        assert out["item_count"] <= 1

    asyncio.run(go())


def test_finalize_decision_fetch_failure_is_non_fatal(tmp_path):
    """拉取失败非致命: finalize 不崩, 但也不兜底发——失败=零决策=空报(2026-08-06)。"""

    class BoomStore:
        async def fetch(self):
            raise RuntimeError("worker down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "A")]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=BoomStore(),
            site_base_url="https://s/",
        )
        # 非致命: 跑完不抛; 但拉取失败视同零决策, 不再兜底自动发
        assert out["item_count"] == 0
        assert out["is_pending"] is True

    asyncio.run(go())


def test_collect_skips_non_relevant_cards(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        ok = _item("https://x/ok", "AI thing")
        junk = _item("https://x/junk", "Not AI").model_copy(update={"relevant": False})
        notifier = FakeNotifier()
        await run_collect_tick("r1", NOW, [ok, junk], "take", db, [notifier])
        sent_links = [card.get("link") for _id, card in notifier.sent_cards]
        assert "https://x/ok" in sent_links
        assert "https://x/junk" not in sent_links

    asyncio.run(go())


def test_select_report_items_gate():
    """纯函数确认门: keep/edit 进, drop/未决策 排除。"""
    from src.core.types import ReviewDecision
    from src.pipeline.tick import select_report_items

    items = [_item(f"https://x/{n}", n) for n in ("keep", "edit", "drop", "undecided")]
    decisions = {
        "https://x/keep": ReviewDecision(action="keep"),
        "https://x/edit": ReviewDecision(action="edit"),
        "https://x/drop": ReviewDecision(action="drop"),
    }
    out = select_report_items(items, decisions)
    assert [it.link for it in out] == ["https://x/keep", "https://x/edit"]


def test_finalize_only_ships_confirmed_items(tmp_path):
    """确认门(review.md/publish.md 推迟给发布层的"未审拦截"): 报告只收显式 keep,
    未决策 + drop 都排除。修 finalize 把未确认内容总结进去的 bug。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [
            _item("https://x/keep", "Keep me"),
            _item("https://x/drop", "Drop me"),
            _item("https://x/undecided", "Never reviewed"),
        ]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        store = FakeDecisionStore({_iid("https://x/keep"): "keep", _iid("https://x/drop"): "drop"})
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        # 只有显式 keep 的进; drop 和 undecided 都不进
        assert out["item_count"] == 1

    asyncio.run(go())


def test_finalize_zero_decisions_publishes_nothing(tmp_path):
    """2026-08-06: 零决策(没碰 TG)不再兜底自动发——用户没审的内容不该结算。
    空报: item_count=0, 且不触发任何 notifier(不写空文件、不发 0 条消息)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [
            _item("https://x/1", "A", genre=Genre.paper),
            _item("https://x/2", "B", genre=Genre.model),
        ]
        await run_collect_tick("r1", NOW, items, "take", db, [FakeNotifier()])
        notifier = FakeNotifier()
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-19",
            items,
            "take",
            db,
            [notifier],
            decision_store=FakeDecisionStore({}),
            site_base_url="https://s/",
        )
        assert out["item_count"] == 0
        assert out["is_pending"] is True
        assert notifier.final_report is None  # 空报不触发通知(不写空文件/不发空消息)

    asyncio.run(go())


def test_finalize_excludes_items_published_on_another_day(tmp_path):
    """已发布去重: 一条在 label 21 发过后, label 22 即便仍被 keep+在窗口内也不再进(修跨天重复)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/a", "A")]
        store = FakeDecisionStore({_iid("https://x/a"): "keep"})
        out1 = await run_finalize_tick(
            "r1",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        out2 = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-22",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        assert out1["item_count"] == 1  # 首日发
        assert out2["item_count"] == 0  # 次日不再发(已在 21 发过)

    asyncio.run(go())


def test_finalize_same_day_rerun_still_ships(tmp_path):
    """同一 date_label 重跑(手动重触发) → 仍发, 不被已发布去重误伤。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/a", "A")]
        store = FakeDecisionStore({_iid("https://x/a"): "keep"})
        out1 = await run_finalize_tick(
            "r1",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        out2 = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        assert out1["item_count"] == 1 and out2["item_count"] == 1

    asyncio.run(go())


def test_published_items_db_roundtrip(tmp_path):
    """db: mark_published + already_published_elsewhere(按 item_id, 排除其它 label)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await db.mark_published(["a", "b"], "2026-06-21")
        # 同 label 不算"别处发过"; 不同 label 才算
        assert await db.already_published_elsewhere(["a", "b", "c"], "2026-06-21") == set()
        assert await db.already_published_elsewhere(["a", "b", "c"], "2026-06-22") == {"a", "b"}
        # 首发 label 固定(INSERT OR IGNORE): 再 mark 到别 label 不改原 label
        await db.mark_published(["a"], "2026-06-22")
        assert await db.already_published_elsewhere(["a"], "2026-06-21") == set()

    asyncio.run(go())


def test_finalize_applies_kv_decision_by_item_id_without_pending_rows(tmp_path):
    """决策解耦: 即使没发过卡(无 pending_reviews 行)、date 不匹配, KV 决策仍按 item_id 生效。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        items = [_item("https://x/1", "Keep me"), _item("https://x/2", "Drop me")]
        store = FakeDecisionStore({_iid("https://x/2"): "drop"})
        out = await run_finalize_tick(
            "r2",
            NOW,
            "2026-06-21",
            items,
            "take",
            db,
            [FakeNotifier()],
            decision_store=store,
            site_base_url="https://s/",
        )
        # x/2 被 drop, 即便从没 collect 过、date_label 与采集日无关
        assert out["item_count"] <= 1

    asyncio.run(go())


def test_collect_tick_snapshots_scored_item_for_each_relevant_card(tmp_path):
    from src.core.types import ScoredItem

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        ok = _item("https://x/1", "A")
        junk = _item("https://x/junk", "Not AI").model_copy(update={"relevant": False})
        await run_collect_tick("r1", NOW, [ok, junk], "take", db, [FakeNotifier()])
        snaps = await db.get_snapshots([_iid("https://x/1"), _iid("https://x/junk")])
        assert set(snaps) == {_iid("https://x/1")}
        restored = ScoredItem.model_validate_json(snaps[_iid("https://x/1")])
        assert restored.link == "https://x/1"
        assert restored.score == 80
        assert "body" not in restored.model_dump()  # 存的是解读前的条目

    asyncio.run(go())


def test_collect_tick_snapshot_uses_pre_interpret_scored_item(tmp_path):
    """I1: 解读会把内容确定性罚分叠进 score; 快照必须是解读的输入, 否则 finalize 重罚。"""
    from src.core.types import ScoredItem

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        interpreted = _item("https://x/1", "A").model_copy(
            update={"score": 65, "score_breakdown": {"技术价值": 80.0, "内容确定性": -15.0}}
        )
        scored = ScoredItem.model_validate(
            _item("https://x/1", "A").model_dump(include=set(ScoredItem.model_fields))
        )
        await run_collect_tick(
            "r1", NOW, [interpreted], "take", db, [FakeNotifier()], scored_items=[scored]
        )
        snap = ScoredItem.model_validate_json(
            (await db.get_snapshots([_iid("https://x/1")]))[_iid("https://x/1")]
        )
        assert snap.score == 80
        assert "内容确定性" not in snap.score_breakdown

    asyncio.run(go())


def test_collect_tick_snapshot_failure_does_not_block_card(tmp_path):
    class _BrokenSnapshots(Database):
        async def upsert_snapshot(self, *a, **k):
            raise RuntimeError("disk full")

    async def go():
        db = _BrokenSnapshots(str(tmp_path / "s.db"))
        await db.init()
        notifier = FakeNotifier()
        await run_collect_tick("r1", NOW, [_item("https://x/1", "A")], "take", db, [notifier])
        assert len(notifier.sent_cards) == 1

    asyncio.run(go())


PREF = "agnes:agnes-2.5-flash"  # config/publish.yaml preferred_issue_model


def _reinterpreter(calls, fail=(), irrelevant=(), model_of=None):
    """假 reinterpret: 记录收到的条目; 按 link 制造失败/不相关/指定模型。"""
    model_of = model_of or {}

    def f(items):
        calls.append([it.link for it in items])
        out = []
        for it in items:
            upd = {"model": model_of.get(it.link, PREF)}
            if it.link in fail:
                upd.update(interpretation_status="extractive_fallback", fallback_reason="RateLimit")
            if it.link in irrelevant:
                upd["relevant"] = False
            out.append(_item(it.link, it.title_en).model_copy(update=upd))
        return out

    return f


async def _seed(db, links):
    await run_collect_tick("r1", NOW, [_item(u, u) for u in links], "take", db, [FakeNotifier()])


def _finalize(db, decisions, reinterpret, run_id="r2", date_label="2026-06-19", store=None):
    return run_finalize_tick(
        run_id,
        NOW,
        date_label,
        [],
        None,
        db,
        [FakeNotifier()],
        decision_store=store or FakeDecisionStore(decisions),
        reinterpret=reinterpret,
    )


def test_finalize_reinterprets_only_kept_snapshots(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1", "https://x/2", "https://x/3"])
        calls = []
        out = await _finalize(
            db,
            {_iid("https://x/1"): "keep", _iid("https://x/2"): "drop"},
            _reinterpreter(calls),
        )
        assert calls == [["https://x/1"]]  # 只解读 keep, 不碰 drop / 未决策
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {}

    asyncio.run(go())


def test_finalize_skips_with_reasons(tmp_path, caplog):
    import json as _json
    import logging

    caplog.set_level(logging.INFO, logger="ai-newsday")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/ok", "https://x/fail", "https://x/junk", "https://x/other"])
        decisions = {
            _iid(u): "keep"
            for u in ["https://x/ok", "https://x/fail", "https://x/junk", "https://x/other"]
        }
        decisions[_iid("https://x/nosnap")] = "keep"
        out = await _finalize(
            db,
            decisions,
            _reinterpreter(
                [],
                fail={"https://x/fail"},
                irrelevant={"https://x/junk"},
                model_of={"https://x/other": "modelscope:other"},
            ),
        )
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {
            "no_snapshot": 1,
            "interpret_failed": 1,
            "irrelevant": 1,
            "model_mismatch": 1,
        }

    asyncio.run(go())
    events = [_json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{")]
    skipped = [e for e in events if e["event"] == "finalize_item_skipped"]
    assert {e["reason"] for e in skipped} == {
        "no_snapshot",
        "interpret_failed",
        "irrelevant",
        "model_mismatch",
    }
    fail = next(e for e in skipped if e["reason"] == "interpret_failed")
    assert fail["error"] == "RateLimit"
    assert any(e["event"] == "finalize_summary" and e["kept"] == 5 for e in events)


def test_finalize_logs_rule_cut_when_keeps_exceed_total_limit(tmp_path):
    """keep 13 条 news(配额 news:1, total_limit 12)→ 只发 1 条, 12 条 rule_cut, 不顺延。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        links = [f"https://x/n{i}" for i in range(13)]
        await _seed(db, links)
        out = await _finalize(db, {_iid(u): "keep" for u in links}, _reinterpreter([]))
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {"rule_cut": 12}

    asyncio.run(go())


def test_finalize_keeps_drop_feedback_from_snapshots(tmp_path):
    """drop 条目不解读, 但负反馈必须照样入账(否则权重只升不降)。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1", "https://x/2"])
        await _finalize(
            db,
            {_iid("https://x/1"): "keep", _iid("https://x/2"): "drop"},
            _reinterpreter([]),
        )
        import aiosqlite

        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute("SELECT link, action FROM feedback_events") as cur:
                rows = set(await cur.fetchall())
        assert rows == {("https://x/1", "keep"), ("https://x/2", "drop")}

    asyncio.run(go())


def test_finalize_snapshot_read_error_skips_all_without_crash(tmp_path):
    class _BrokenRead(Database):
        async def get_snapshots(self, item_ids):
            raise RuntimeError("no such table")

    async def go():
        db = _BrokenRead(str(tmp_path / "s.db"))
        await db.init()
        out = await _finalize(db, {_iid("https://x/1"): "keep"}, _reinterpreter([]))
        assert out["item_count"] == 0
        assert out["skipped_by_reason"] == {"no_snapshot": 1}

    asyncio.run(go())


def test_finalize_invalid_snapshot_is_no_snapshot_not_crash(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/good"])
        await db.upsert_snapshot(_iid("https://x/bad"), "2026-06-19", '{"not": "a scored item"}')
        out = await _finalize(
            db,
            {_iid("https://x/good"): "keep", _iid("https://x/bad"): "keep"},
            _reinterpreter([]),
        )
        assert out["item_count"] == 1
        assert out["skipped_by_reason"] == {"no_snapshot": 1}

    asyncio.run(go())


async def _feedback_rows(db):
    import aiosqlite

    async with aiosqlite.connect(db._path) as conn:
        async with conn.execute("SELECT run_id, link, action FROM feedback_events") as cur:
            return set(await cur.fetchall())


def test_finalize_settles_decisions_once_across_nights(tmp_path):
    """C1: KV 决策保留 7 天, 第二晚同一批决策不能再解读、再计反馈。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1", "https://x/2"])
        store = FakeDecisionStore({_iid("https://x/1"): "keep", _iid("https://x/2"): "drop"})
        calls = []
        n1 = await _finalize(
            db, None, _reinterpreter(calls), run_id="n1", date_label="2026-06-19", store=store
        )
        assert n1["item_count"] == 1
        assert calls == [["https://x/1"]]
        rows_n1 = await _feedback_rows(db)
        n2 = await _finalize(
            db, None, _reinterpreter(calls), run_id="n2", date_label="2026-06-20", store=store
        )
        assert calls == [["https://x/1"]]  # 第二晚一条都不解读
        assert await _feedback_rows(db) == rows_n1  # 不新增反馈事件
        assert n2["item_count"] == 0

    asyncio.run(go())


def test_finalize_same_label_rerun_reprocesses_settled_decisions(tmp_path):
    """C1: 同一 date_label 重跑(手动补跑)照样重新处理。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/1"])
        store = FakeDecisionStore({_iid("https://x/1"): "keep"})
        calls = []
        await _finalize(db, None, _reinterpreter(calls), run_id="a", store=store)
        out = await _finalize(db, None, _reinterpreter(calls), run_id="b", store=store)
        assert calls == [["https://x/1"], ["https://x/1"]]
        assert out["item_count"] == 1

    asyncio.run(go())


def test_finalize_skips_decisions_recorded_before_settlement_existed(tmp_path):
    """C1: 上线第一晚, 旧 finalize 已记进 decisions 表的决策视为已结算, 不再处理。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        await _seed(db, ["https://x/old", "https://x/new"])
        await db.record_decisions({_iid("https://x/old"): "keep"}, ts=NOW.isoformat())
        calls = []
        out = await _finalize(
            db,
            {_iid("https://x/old"): "keep", _iid("https://x/new"): "keep"},
            _reinterpreter(calls),
        )
        assert calls == [["https://x/new"]]
        assert out["item_count"] == 1
        assert {link for _, link, _ in await _feedback_rows(db)} == {"https://x/new"}

    asyncio.run(go())
