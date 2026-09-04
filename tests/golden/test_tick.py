import asyncio
import hashlib
from datetime import datetime, timezone

from src.adapters.decisions.worker import FakeDecisionStore
from src.core.types import (
    Evidence,
    Genre,
    InterpretConfig,
    InterpretedItem,
)
from src.notifiers import FakeNotifier
from src.pipeline.tick import run_collect_tick, run_finalize_tick
from src.state.db import Database
from tests.fakes import DEFAULT_PUBLISHER

NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-06-05"


def _make_item(link, source="hf-models", st=Genre.model, cluster_id=None, signals=None):
    return InterpretedItem(
        title_en="DeepSeek V4 released",
        link=link,
        source=source,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=NOW,
        raw_summary="A.",
        cluster_id=cluster_id or link,
        related_links=[],
        score=90,
        score_breakdown={
            "可见指标": 15.0,
            "机构影响力": 15.0,
            "一手性": 18.0,
            "技术价值": 14.0,
            "产业影响": 10.0,
            "扩散潜力": 9.0,
            "时效": 10.0,
            "惩罚": 0.0,
            "读者相关度": 0.0,
        },
        signals=signals or {"likes": 4622},
        is_explore=False,
        title="DeepSeek V4 发布",
        body="旗舰模型发布，可替换 API，护城河变薄。",
        tags=["#模型"],
        evidence=[Evidence(claim="发布了", anchor=link)],
        interpretation_status="ok",
        eligible_for_must_read=True,
        review_action=None,
        was_edited=False,
        edited_fields=[],
    )


def test_collect_tick_pushes_cards_and_saves_to_db(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1", cluster_id="c1")
        await run_collect_tick(
            run_id="r1",
            now=NOW,
            interpreted_items=[item],
            daily_take="今天看点。",
            db=db,
            notifiers=[notifier],
        )
        assert len(notifier.sent_cards) == 1
        rows = await db.get_pending_reviews_for_date(TODAY)
        assert len(rows) == 1
        assert rows[0]["link"] == "https://a/1"
        assert rows[0]["status"] == "pending"

    asyncio.run(go())


def test_collect_tick_skips_already_sent_item(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1")
        await run_collect_tick("r1", NOW, [item], None, db, [notifier])
        await run_collect_tick("r2", NOW, [item], None, db, [notifier])
        assert len(notifier.sent_cards) == 1

    asyncio.run(go())


def test_finalize_tick_builds_report_and_notifies(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        await db.insert_run("r1", "collect")
        for item_id, link, status in [
            ("id1", "https://a/1", "keep"),
            ("id2", "https://a/2", "pending"),
        ]:
            await db.upsert_pending_review(
                item_id=item_id,
                run_id="r1",
                link=link,
                source="openai",
                title_en="X",
                title_zh="X",
                summary_zh="s",
                takeaway="t",
                hot_take="h",
                score=80,
                signals={},
                date=TODAY,
            )
            if status != "pending":
                await db.update_decision(item_id, status)
        notifier = FakeNotifier()
        items = [
            _make_item(link, source="openai", st=Genre.announcement, signals={})
            for link in ["https://a/1", "https://a/2"]
        ]
        # 确认门: 报告只收显式 keep 的条目, 故提供 a/1=keep 的远程决策
        keep_id = hashlib.sha256(b"https://a/1").hexdigest()[:16]
        result = await run_finalize_tick(
            run_id="r2",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take=None,
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore({keep_id: "keep"}),
        )
        # a/1 keep 进, a/2 pending(无远程决策) 不进
        assert result["item_count"] == 1
        assert notifier.final_report is not None
        assert "AI Daily" in notifier.final_report

    asyncio.run(go())


def test_finalize_tick_returns_dict_keys(tmp_path):
    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        result = await run_finalize_tick(
            run_id="r1",
            now=NOW,
            date_label=TODAY,
            interpreted_items=[],
            daily_take=None,
            db=db,
            notifiers=[notifier],
        )
        for k in ("run_id", "date_label", "item_count", "is_pending"):
            assert k in result

    asyncio.run(go())


def test_finalize_tick_persists_feedback_and_is_idempotent(tmp_path):
    async def go():
        import aiosqlite

        from src.pipeline.tick import _item_id

        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        item = _make_item("https://a/1", source="hf-models", cluster_id="c1")

        # seed a 'keep' decision for this item under run id "r-fin"
        iid = _item_id(item)
        await db.insert_run("r-fin", "finalize")
        await db.upsert_pending_review(
            item_id=iid,
            run_id="r-fin",
            link=item.link,
            source=item.source,
            title_en=item.title_en,
            title_zh=item.title,
            summary_zh=item.body,
            takeaway="",
            hot_take="",
            score=item.score,
            signals=item.signals,
            date=TODAY,
        )
        # webhook 模型: 决策在 KV(decision_store), 按 item_id 匹配本报条目
        store = FakeDecisionStore({iid: "keep"})

        # run finalize twice with the SAME run_id
        for _ in range(2):
            await run_finalize_tick(
                run_id="r-fin",
                now=NOW,
                date_label=TODAY,
                interpreted_items=[item],
                daily_take="x",
                db=db,
                notifiers=[notifier],
                decision_store=store,
            )

        # keep -> 升权 from baseline 1.0 by step 0.2
        weights = await db.get_quality_weights()
        assert weights["hf-models"] == 1.2

        # idempotent: exactly one event row for (run_id, link)
        async with aiosqlite.connect(db._path) as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM feedback_events WHERE run_id=? AND link=?",
                ("r-fin", item.link),
            ) as cur:
                (n,) = await cur.fetchone()
        assert n == 1

    asyncio.run(go())


def test_finalize_tick_carries_the_generated_title_into_the_report(tmp_path):
    """回归(#130): #129 把 wechat_title 一路穿过类型, 却漏改 cli/tick 里 review()
    的调用点——参数有默认值 None 于是静默用了默认, 结果 2026-09-01 线上
    daily_take_done 报 title_generated: true, 成品标题却仍是 "AI Daily · <date>"。

    此前的单测直接构造 ReviewResult 赋值 wechat_title, 正好绕过 review() 调用点,
    所以全绿也没抓到。这条必须走 run_finalize_tick 真实路径。"""

    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        items = [_make_item("https://a/1", source="openai", st=Genre.announcement, signals={})]
        keep_id = hashlib.sha256(b"https://a/1").hexdigest()[:16]
        await run_finalize_tick(
            run_id="r3",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take="今日亮点：甲发布 X。详见正文，参考链接见文末。",
            wechat_title="甲发布X | 乙提出Y【AI日报】",
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore({keep_id: "keep"}),
        )
        assert notifier.final_report is not None
        assert "甲发布X | 乙提出Y【AI日报】" in notifier.final_report
        assert f"AI Daily · {TODAY}" not in notifier.final_report

    asyncio.run(go())


def test_daily_take_is_not_double_prefixed(tmp_path):
    """回归(#130): 渲染器硬编码 `> **今日看点**：`, 而新摘要自带 `今日亮点：`,
    2026-09-01 成品里出现 `今日看点：今日亮点：`。"""

    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        items = [_make_item("https://a/1", source="openai", st=Genre.announcement, signals={})]
        keep_id = hashlib.sha256(b"https://a/1").hexdigest()[:16]
        await run_finalize_tick(
            run_id="r4",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take="今日亮点：甲发布 X。详见正文，参考链接见文末。",
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore({keep_id: "keep"}),
        )
        assert "今日看点：今日亮点：" not in notifier.final_report
        assert "今日亮点：" in notifier.final_report

    asyncio.run(go())


def test_finalize_title_reflects_only_items_that_survive_quota(tmp_path):
    """回归(#139): 2026-09-02 线上标题引用了 ChatGPT Ads / Qwen3.8-Flash-Next,
    但那两条从未出现在最终发布的六条正文里——generate_daily_head 此前在
    interpret() 阶段用全量解读池生成, 早于 build_report() 的地板/配额/故事线
    合并过滤。这里用真实 config/publish.yaml 的 min_display_score(40) 地板
    天然制造一次淘汰: 两条都 keep, 一条 90 分幸存、一条 10 分被地板砍掉,
    断言喂给 LLM 的 prompt 只包含幸存条目的标题, 被砍条目的标题绝不出现。"""

    class _CapturingLLM:
        def __init__(self):
            self.prompts: list[str] = []

        def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
            self.prompts.append(prompt)
            return (
                '{"title": "生成的标题【AI日报】", '
                '"digest": "今日亮点：生成的摘要。详见正文，参考链接见文末。"}'
            )

    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        survivor = _make_item("https://a/1", st=Genre.announcement).model_copy(
            update={"title": "幸存条目标题", "score": 90}
        )
        dropped = _make_item("https://a/2", st=Genre.announcement).model_copy(
            update={"title": "被地板砍掉的条目标题", "score": 10}
        )
        decisions = {
            hashlib.sha256(b"https://a/1").hexdigest()[:16]: "keep",
            hashlib.sha256(b"https://a/2").hexdigest()[:16]: "keep",
        }
        llm = _CapturingLLM()
        icfg = InterpretConfig()
        result = await run_finalize_tick(
            run_id="r6",
            now=NOW,
            date_label=TODAY,
            interpreted_items=[survivor, dropped],
            daily_take=None,
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore(decisions),
            llm=llm,
            interpret_config=icfg,
        )
        assert result["item_count"] == 1, "两条都 keep, 但地板下的那条不该进最终报告"
        assert len(llm.prompts) == 1, "标题/摘要只应生成一次"
        prompt = llm.prompts[0]
        assert "幸存条目标题" in prompt
        assert "被地板砍掉的条目标题" not in prompt
        assert notifier.final_report is not None
        assert "生成的标题【AI日报】" in notifier.final_report

    asyncio.run(go())


def test_finalize_without_llm_keeps_prior_behavior(tmp_path):
    """未传 llm(如现有旧调用点)时行为不变: 用调用方给的 wechat_title/daily_take,
    不尝试重新生成——向后兼容, 不强迫每个调用点都升级。"""

    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        items = [_make_item("https://a/1", st=Genre.announcement)]
        keep_id = hashlib.sha256(b"https://a/1").hexdigest()[:16]
        await run_finalize_tick(
            run_id="r7",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take="旧摘要。",
            wechat_title="旧标题【AI日报】",
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore({keep_id: "keep"}),
        )
        assert "旧标题【AI日报】" in notifier.final_report

    asyncio.run(go())


def test_finalize_tick_produces_the_wechat_version_too(tmp_path):
    """spec 2026-08-31 §1: 流水线要同时产出公众号版, 而不是每天人工从网站版转换。

    转换这一步至今是手工的, 漏目录(2026-09-03)、漏标签清洗、漏摘要都出在那里——
    出错的每一次都是手工环节, 不是流水线。这条走真实 run_finalize_tick 路径,
    盯的就是"接线漏了但类型看着没问题"这类静默失效(#130 的教训)。"""

    async def go():
        db = Database(str(tmp_path / "state.db"))
        await db.init()
        notifier = FakeNotifier()
        items = [_make_item("https://a/1", source="openai", st=Genre.announcement, signals={})]
        keep_id = hashlib.sha256(b"https://a/1").hexdigest()[:16]
        await run_finalize_tick(
            run_id="r-wechat",
            now=NOW,
            date_label=TODAY,
            interpreted_items=items,
            daily_take="今日亮点：甲发布 X。详见正文，参考链接见文末。",
            wechat_title="甲发布X【AI日报】",
            db=db,
            notifiers=[notifier],
            decision_store=FakeDecisionStore({keep_id: "keep"}),
        )
        w = notifier.final_wechat
        assert w, "公众号版没产出——接线漏了"
        assert w.split("\n", 1)[0] == "甲发布X【AI日报】"
        assert "## 目录" in w
        assert "draft:" not in w and "RSS · 历史归档" not in w
        assert "](http" not in w.split("## 参考链接", 1)[1]

    asyncio.run(go())
