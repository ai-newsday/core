"""零产出告警 (#169)。

x-extension 曾经连续 17 天产出 0 而无人察觉, 当时记下的结论就是"非致命失败必须配
零产出告警"——那个告警一直没做, 于是 2026-09-07 同一件事再次发生: 五个 X 源全部
`source_fetch_success, item_count: 0`, **成功而不是失败**, 日志里一切正常。

发卡池是固定取前 100, 好源交白卷时位置不会空着, 会被二手新闻填满(实测二手占比
从 24% 涨到 77%)。所以静默归零不是"少几条", 是换了一份报纸。
"""

import asyncio
import json
from datetime import datetime, timezone

from src.core.types import SourceReport, ZeroYieldAlertConfig
from src.notifiers import FakeNotifier
from src.pipeline.collect import check_zero_yield
from src.pipeline.tick import run_collect_tick
from src.state.db import Database


def test_first_zero_run_does_not_alert():
    """单次归零很正常——三小时内某个源没有新东西不值得打扰。"""
    state, alerts = check_zero_yield({"x_list": 0}, {}, threshold=3)
    assert alerts == []
    assert state["x_list"] == 1


def test_alerts_once_the_threshold_is_reached():
    state, alerts = check_zero_yield({"x_list": 0}, {"x_list": 2}, threshold=3)
    assert alerts == ["x_list"]
    assert state["x_list"] == 3


def test_does_not_alert_again_while_the_outage_continues():
    """一次停摆只报一次。17 天的停摆报 17×8 次消息只会让人开始忽略告警。"""
    state, alerts = check_zero_yield({"x_list": 0}, {"x_list": 3}, threshold=3)
    assert alerts == []
    assert state["x_list"] == 4


def test_any_yield_resets_the_counter():
    state, alerts = check_zero_yield({"x_list": 5}, {"x_list": 2}, threshold=3)
    assert state["x_list"] == 0
    assert alerts == []


def test_recovery_then_a_new_outage_alerts_again():
    """恢复之后再次停摆是新的一次事故, 必须再报一次。"""
    state, _ = check_zero_yield({"x_list": 5}, {"x_list": 7}, threshold=3)
    for _ in range(2):
        state, alerts = check_zero_yield({"x_list": 0}, state, threshold=3)
        assert alerts == []
    state, alerts = check_zero_yield({"x_list": 0}, state, threshold=3)
    assert alerts == ["x_list"]


def test_groups_are_independent():
    state, alerts = check_zero_yield(
        {"x_list": 0, "hf_papers": 12}, {"x_list": 2, "hf_papers": 2}, threshold=3
    )
    assert alerts == ["x_list"]
    assert state["hf_papers"] == 0


def test_multiple_groups_can_alert_in_the_same_run():
    state, alerts = check_zero_yield(
        {"x_list": 0, "hf_papers": 0}, {"x_list": 2, "hf_papers": 2}, threshold=3
    )
    assert sorted(alerts) == ["hf_papers", "x_list"]


def test_unwatched_groups_are_left_untouched():
    """状态里可能留着已经不再监控的组, 不该凭空冒出来。"""
    state, alerts = check_zero_yield({"x_list": 0}, {"old_group": 9}, threshold=3)
    assert state["old_group"] == 9
    assert alerts == []


def test_threshold_of_one_alerts_immediately():
    _, alerts = check_zero_yield({"x_list": 0}, {}, threshold=1)
    assert alerts == ["x_list"]


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def _reports(x_count):
    return [
        SourceReport(name="x-ai-lab", status="working", item_count=x_count, elapsed_ms=1),
        SourceReport(name="techcrunch-ai", status="working", item_count=13, elapsed_ms=1),
    ]


ADAPTER_OF = {"x-ai-lab": "x_list", "techcrunch-ai": "rss"}


def test_collect_tick_alerts_after_the_threshold_and_persists_state(tmp_path):
    """接线测试: 纯函数全绿但没接进 tick 的话, 静默归零照样静默。
    盯的就是"写了没人调用"这类失效——今天已经踩过一次。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        cfg = ZeroYieldAlertConfig(adapters=["x_list"], consecutive_runs=2)
        n = FakeNotifier()
        for _ in range(2):
            await run_collect_tick(
                run_id="r",
                now=NOW,
                interpreted_items=[],
                daily_take=None,
                db=db,
                notifiers=[n],
                source_reports=_reports(0),
                zero_yield_config=cfg,
                adapter_of=ADAPTER_OF,
            )
        assert len(n.alerts) == 1, "连续两次归零应当恰好报一次"
        assert "x_list" in n.alerts[0]
        assert json.loads(await db.get_kv("zero_yield_state"))["x_list"] == 2

    asyncio.run(go())


def test_collect_tick_does_not_alert_while_the_source_yields(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        cfg = ZeroYieldAlertConfig(adapters=["x_list"], consecutive_runs=2)
        n = FakeNotifier()
        for _ in range(4):
            await run_collect_tick(
                run_id="r",
                now=NOW,
                interpreted_items=[],
                daily_take=None,
                db=db,
                notifiers=[n],
                source_reports=_reports(30),
                zero_yield_config=cfg,
                adapter_of=ADAPTER_OF,
            )
        assert n.alerts == []

    asyncio.run(go())


def test_alert_failure_never_breaks_the_collect_tick(tmp_path):
    """告警本身不能把采集弄挂——那会把"有个源坏了"升级成"整条流水线坏了"。"""

    class _Boom(FakeNotifier):
        async def send_alert(self, text):
            raise RuntimeError("telegram down")

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        cfg = ZeroYieldAlertConfig(adapters=["x_list"], consecutive_runs=1)
        await run_collect_tick(
            run_id="r",
            now=NOW,
            interpreted_items=[],
            daily_take=None,
            db=db,
            notifiers=[_Boom()],
            source_reports=_reports(0),
            zero_yield_config=cfg,
            adapter_of=ADAPTER_OF,
        )  # 不抛异常即通过

    asyncio.run(go())


def test_no_config_means_no_check(tmp_path):
    """向后兼容: 不传配置时行为与接线前完全一致。"""

    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        n = FakeNotifier()
        await run_collect_tick(
            run_id="r", now=NOW, interpreted_items=[], daily_take=None, db=db, notifiers=[n]
        )
        assert n.alerts == []
        assert await db.get_kv("zero_yield_state") is None

    asyncio.run(go())
