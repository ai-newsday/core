"""interpret 的每条解读并发执行 (KANBAN §3 P0)。

2026-09-03/04 连续两晚 finalize 跑 40-60 分钟。根因: `interpret()` 是纯顺序
for 循环, 一条一条等 LLM 往返, 发卡池 100 就是 100 次串行。把发卡池缩回去会把
刚恢复的 X 覆盖率搭进去, 所以要并发跑: 调用次数与成本完全不变, 墙钟从
「N × 单条延迟」降到「N/并发 × 单条延迟」。
"""

import json
import logging
import threading
from datetime import datetime, timezone

from src.core.types import Genre, InterpretConfig, Publisher, RunContext, ScoredItem
from src.pipeline.interpret import interpret

NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="c", now=NOW, logger=logging.getLogger("test.interpret.conc"))


def _scored(link):
    return ScoredItem(
        title_en=f"item {link}",
        link=link,
        source="src",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary="summary.",
        cluster_id="c1",
        related_links=[],
        score=80,
        score_breakdown={},
        is_explore=False,
    )


def _ok_json(anchor):
    return json.dumps(
        {
            "title": f"标题 {anchor}",
            "body": "正文。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "事实", "anchor": anchor}],
            "relevant": True,
        }
    )


class _BarrierLLM:
    """前 `parties` 个并发请求必须同时在场才放行。

    顺序执行时第一个调用会一直等不到同伴, 撞上 barrier 超时 -> 测试失败。
    比"计时后断言更快"可靠: 不看机器快慢, 直接判定有没有真的同时在飞。
    """

    def __init__(self, parties: int):
        self._barrier = threading.Barrier(parties, timeout=5)
        self.timed_out = False

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        # daily head 那次调用是单独一次, 不参与 barrier
        if "{{items}}" in prompt or "今日条目" in prompt:
            return json.dumps({"title": "T【AI日报】", "digest": "今日亮点：X。"})
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            self.timed_out = True
        anchor = prompt.split("L=")[-1].split()[0] if "L=" in prompt else "https://x/1"
        out = _ok_json(anchor)
        if validator is not None:
            validator(out)
        return out


def test_items_are_interpreted_concurrently():
    """回归: 顺序循环下这个测试会卡满 barrier 超时。"""
    items = [_scored(f"https://x/{i}") for i in range(4)]
    llm = _BarrierLLM(parties=4)
    cfg = InterpretConfig(concurrency=4)
    res = interpret(items, cfg, _ctx(), llm)
    assert not llm.timed_out, "4 条应当同时在飞, 实际是一条一条串着跑"
    assert len(res.interpreted_items) == 4


class _OrderLLM:
    """故意让靠后的条目先返回, 逼出"谁先回来谁排前面"的顺序 bug。"""

    def __init__(self):
        self._seen = 0
        self._lock = threading.Lock()

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        if "今日条目" in prompt:
            return json.dumps({"title": "T【AI日报】", "digest": "今日亮点：X。"})
        with self._lock:
            self._seen += 1
            n = self._seen
        # 先进来的多睡一会, 让后进来的先完成
        threading.Event().wait(0.05 if n <= 2 else 0.0)
        anchor = prompt.split("L=")[-1].split()[0] if "L=" in prompt else "https://x/1"
        out = _ok_json(anchor)
        if validator is not None:
            validator(out)
        return out


def test_result_order_follows_input_order_not_completion_order():
    """并发只该改耗时, 不该改顺序——下游(发布配额、参考链接编号)依赖这个次序。"""
    links = [f"https://x/{i}" for i in range(6)]
    res = interpret(
        [_scored(link) for link in links], InterpretConfig(concurrency=6), _ctx(), _OrderLLM()
    )
    assert [it.link for it in res.interpreted_items] == links


def test_concurrency_one_still_works():
    """并发度 1 = 退化成原来的顺序执行, 保留这条退路。"""
    items = [_scored(f"https://x/{i}") for i in range(3)]
    res = interpret(items, InterpretConfig(concurrency=1), _ctx(), _BarrierLLM(parties=1))
    assert len(res.interpreted_items) == 3


def test_one_failing_item_does_not_take_down_the_batch():
    """一条挂掉只该自己回退成抽取式, 不能连累同批其它条目(并发下更要确认)。"""

    class _OneBadLLM:
        def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
            if "今日条目" in prompt:
                return json.dumps({"title": "T【AI日报】", "digest": "今日亮点：X。"})
            if "https://x/1" in prompt:
                raise RuntimeError("boom")
            anchor = prompt.split("L=")[-1].split()[0] if "L=" in prompt else "https://x/0"
            out = _ok_json(anchor)
            if validator is not None:
                validator(out)
            return out

    items = [_scored(f"https://x/{i}") for i in range(3)]
    res = interpret(items, InterpretConfig(concurrency=3), _ctx(), _OneBadLLM())
    by_link = {it.link: it for it in res.interpreted_items}
    assert by_link["https://x/1"].interpretation_status == "extractive_fallback"
    assert by_link["https://x/0"].interpretation_status == "ok"
    assert by_link["https://x/2"].interpretation_status == "ok"
