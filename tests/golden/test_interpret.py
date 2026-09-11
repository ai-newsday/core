import json
import logging
from datetime import datetime, timezone

from src.core.types import Genre, InterpretConfig, Publisher, RunContext, ScoredItem
from src.pipeline.interpret import interpret
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-interpret"))


def _scored(link, title_en="X released", score=80, related=None, raw="A summary."):
    return ScoredItem(
        title_en=title_en,
        link=link,
        source="src",
        genre=Genre.model,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=raw,
        cluster_id="evt-1",
        related_links=related or [],
        score=score,
        score_breakdown={"机构影响力": float(score)},
        is_explore=False,
    )


def _ok_json(anchor):
    return json.dumps(
        {
            "title": "中文标题",
            "body": "中文正文，先讲事实，再落到对从业者的意义，可用一句克制的判断收尾。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "事实", "anchor": anchor}],
            "relevant": True,
        }
    )


# Case 1 (spec §9.1): happy full fields
def test_golden_happy_full_fields():
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider(
        {"https://a/1": _ok_json("https://a/1")},
        # 摘要用真实形状(带固定收尾): enforce_digest 会给缺收尾的补上(#174),
        # 夹具若不带收尾, 这条 golden 测的就不是"原样透传"而是"被修补"。
        default=json.dumps(
            {
                "title": "甲发布X | 乙提出Y【AI日报】",
                "digest": "今日亮点：甲发布 X。详见正文，参考链接见文末。",
            }
        ),
    )
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    one = res.interpreted_items[0]
    assert one.interpretation_status == "ok"
    assert len(one.tags) == 3 and one.eligible_for_must_read is True
    assert res.interpreted_count == 1 and res.fallback_count == 0
    assert res.daily_take == "今日亮点：甲发布 X。详见正文，参考链接见文末。"


# Case 2 (spec §9.2): wrong tag count -> fallback
def test_golden_wrong_tags_falls_back():
    bad = json.dumps(
        {
            "title": "t",
            "body": "正文内容。",
            "tags": ["#one"],
            "evidence": [],
        }
    )
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider(
        {"https://a/1": bad},
        default=json.dumps({"title": "甲发布X | 乙提出Y【AI日报】", "digest": "h"}),
    )
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "extractive_fallback"
    assert res.interpreted_items[0].tags == []


# Case 3 (spec §9.3): total LLM failure -> all fallback, daily None
def test_golden_total_failure_all_fallback():
    items = [
        _scored("https://a/1", title_en="T1", raw="R1."),
        _scored("https://b/2", title_en="T2", raw="R2."),
    ]
    res = interpret(items, InterpretConfig(), _ctx(), FailingLLMProvider())
    assert res.fallback_count == 2 and res.interpreted_count == 0
    assert all(i.interpretation_status == "extractive_fallback" for i in res.interpreted_items)
    assert res.interpreted_items[0].title == "T1"
    assert res.interpreted_items[0].body == "R1."
    assert res.daily_take is None
    # zero fabrication: no tags, no evidence, body is extractive raw_summary
    assert all(i.tags == [] and i.evidence == [] for i in res.interpreted_items)


# Case 4 (spec §9.4): evidence empty -> not must-read
def test_golden_empty_evidence_not_must_read():
    j = json.dumps(
        {
            "title": "t",
            "body": "正文有内容但无 evidence。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [],
        }
    )
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider(
        {"https://a/1": j},
        default=json.dumps({"title": "甲发布X | 乙提出Y【AI日报】", "digest": "h"}),
    )
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "ok"
    assert res.interpreted_items[0].eligible_for_must_read is False


# Case 5 (spec §9.5): empty input -> silent, LLM not called
def test_golden_empty_input_silent_no_llm_call():
    llm = FailingLLMProvider()
    res = interpret([], InterpretConfig(), _ctx(), llm)
    assert res.is_silent is True and res.interpreted_items == []
    assert res.daily_take is None and res.input_count == 0
    assert llm.calls == []  # never called on silent


# Case 6 (spec §9.6): illegal anchor dropped + determinism
def test_golden_illegal_anchor_dropped_and_deterministic():
    j = _ok_json("https://evil/x")  # anchor not in link∪related
    items = [_scored("https://a/1", related=["https://r/1"])]
    llm = FakeLLMProvider(
        {"https://a/1": j},
        default=json.dumps({"title": "甲发布X | 乙提出Y【AI日报】", "digest": "h"}),
    )
    res1 = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res1.interpreted_items[0].evidence == []
    assert res1.interpreted_items[0].eligible_for_must_read is False
    llm2 = FakeLLMProvider(
        {"https://a/1": j},
        default=json.dumps({"title": "甲发布X | 乙提出Y【AI日报】", "digest": "h"}),
    )
    res2 = interpret(items, InterpretConfig(), _ctx(), llm2)
    assert [e.model_dump() for e in res2.interpreted_items[0].evidence] == []
    assert res1.interpreted_items[0].title == res2.interpreted_items[0].title


# Case 7 (spec §M2-B1): relevant=false from LLM propagates
def test_golden_relevant_false_propagates():
    j = json.dumps(
        {
            "title": "中文标题",
            "body": "正文内容。",
            "tags": ["#a", "#b", "#c"],
            "evidence": [{"claim": "事实", "anchor": "https://a/1"}],
            "relevant": False,
        }
    )
    items = [_scored("https://a/1")]
    llm = FakeLLMProvider(
        {"https://a/1": j},
        default=json.dumps({"title": "甲发布X | 乙提出Y【AI日报】", "digest": "h"}),
    )
    res = interpret(items, InterpretConfig(), _ctx(), llm)
    assert res.interpreted_items[0].interpretation_status == "ok"
    assert res.interpreted_items[0].relevant is False


# --- 跳过 interpret 阶段的 head 生成 (2026-09-09) ---


class _CountingLLM:
    def __init__(self):
        self.calls = 0

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        self.calls += 1
        if "今日条目" in prompt:
            return json.dumps(
                {
                    "title": "T【AI日报】",
                    # 4 段: 段数不足会触发补段重试, 那样这条测的就不是调用次数了
                    "digest": "今日亮点：甲发 X；乙提 Y；丙开源 Z；丁上线 W。详见正文，参考链接见文末。",
                }
            )
        out = _ok_json("https://a/1")
        if validator is not None:
            validator(out)
        return out


def test_interpret_can_skip_head_generation():
    """interpret 阶段的标题/摘要在生产里 100% 被 regenerate_wechat_head 覆盖
    (finalize、dry-run 两条路径都无条件重生成), 而它用的是全量解读池——prompt 是
    全系统最大的一个。2026-09-09 实测: agnes 的 8000 token 全烧在推理上、一个正文
    token 没产出, 逐条解读却 100/100 全成功, 就是这个 prompt 太大。

    每天固定失败一次、污染日志、输出还用不上, 所以调用方应当能关掉它。"""
    llm = _CountingLLM()
    res = interpret([_scored("https://a/1")], InterpretConfig(), _ctx(), llm, generate_head=False)
    assert llm.calls == 1, f"只该有逐条解读那一次调用, 实际 {llm.calls}"
    assert res.daily_take is None
    assert len(res.interpreted_items) == 1


def test_interpret_generates_head_by_default():
    """默认仍生成——不传参数的调用方(测试、无 llm 的路径)行为不变。"""
    llm = _CountingLLM()
    res = interpret([_scored("https://a/1")], InterpretConfig(), _ctx(), llm)
    assert llm.calls == 2, "逐条 + head 共两次"
    assert res.daily_take is not None


# --- 记录产出模型 (2026-09-10) ---


class _ModelAwareLLM:
    """带 last_model() 的替身, 模拟真实适配器的线程本地记录。"""

    def __init__(self, model="agnes:agnes-2.0-flash"):
        self._model = model

    def complete_json(self, prompt, *, temperature, max_tokens, validator=None):
        out = _ok_json("https://a/1")
        if validator is not None:
            validator(out)
        return out

    def last_model(self):
        return self._model


def test_interpreted_item_records_the_model_that_wrote_it():
    """2026-09-10 那期 30 条是 agnes + DeepSeek + Qwen 混着写的, 而读者感到的
    "质量参差"至今只能靠感觉——从没记录过谁写的。有了它才能按模型量质量。"""
    from src.pipeline.interpret import interpret_item

    res = interpret_item(
        _scored("https://a/1"), "{{title_en}}", InterpretConfig(), _ModelAwareLLM("agnes:x")
    )
    assert res.interpretation_status == "ok"
    assert res.model == "agnes:x"


def test_fallback_item_records_no_model():
    """回退条目没有哪个模型写过它, 不能误记。"""
    from src.pipeline.interpret import interpret_item
    from tests.fakes import FailingLLMProvider

    res = interpret_item(
        _scored("https://a/1"), "{{title_en}}", InterpretConfig(), FailingLLMProvider()
    )
    assert res.interpretation_status == "extractive_fallback"
    assert res.model is None


def test_llm_without_last_model_still_works():
    """12 个测试替身里只有 1 个接受 **kwargs, 所以不能靠给 complete_json 加参数
    实现这个功能——那会让 11 个替身抛 TypeError。没有 last_model() 的 llm 照常工作。"""
    from src.pipeline.interpret import interpret_item
    from tests.fakes import FakeLLMProvider

    llm = FakeLLMProvider({"https://a/1": _ok_json("https://a/1")})
    res = interpret_item(_scored("https://a/1"), "{{link}}", InterpretConfig(), llm)
    assert res.interpretation_status == "ok"
    assert res.model is None


# --- 解读缓存 (2026-09-11): 同一链接一天只让模型写一次 ---
# collect tick 每天跑多轮, finalize 又从头解读一遍; agnes 被限流到约每分钟 1 次、
# ModelScope 每日额度被白天的 collect 烧光, 当晚 45/60 条回退。缓存让白天的每一轮
# 只补没写过的链接, finalize 基本不用再调模型。


def test_cache_hit_skips_the_llm_and_rebuilds_from_current_item():
    cache = {
        "https://a/1": {
            "parsed": {**json.loads(_ok_json("https://a/1")), "content_certain": False},
            "model": "agnes:agnes-2.0-flash",
            "ts": NOW.isoformat(),
        }
    }
    llm = FailingLLMProvider()
    res = interpret(
        [_scored("https://a/1", score=70)],
        InterpretConfig(),
        _ctx(),
        llm,
        generate_head=False,
        cache=cache,
    )
    one = res.interpreted_items[0]
    assert llm.calls == []
    assert one.interpretation_status == "ok" and one.model == "agnes:agnes-2.0-flash"
    # 扣分按**当次**分数重算, 而不是沿用缓存那一轮的分数
    assert one.score == 55


def test_fresh_success_is_written_to_cache_and_fallback_is_not():
    cache: dict = {}
    llm = FakeLLMProvider({"https://a/1": _ok_json("https://a/1")})
    interpret(
        [_scored("https://a/1"), _scored("https://a/2")],
        InterpretConfig(),
        _ctx(),
        llm,
        generate_head=False,
        cache=cache,
    )
    assert set(cache) == {"https://a/1"}
    assert cache["https://a/1"]["parsed"]["title"] == "中文标题"
    assert cache["https://a/1"]["ts"] == NOW.isoformat()


def test_unusable_cache_entry_falls_through_to_the_llm():
    """prompt/配置改了之后旧条目可能不再合法(比如 tags 数量), 不能因此回退。"""
    cache = {"https://a/1": {"parsed": {"tags": []}, "model": "m", "ts": NOW.isoformat()}}
    llm = FakeLLMProvider({"https://a/1": _ok_json("https://a/1")})
    res = interpret(
        [_scored("https://a/1")],
        InterpretConfig(),
        _ctx(),
        llm,
        generate_head=False,
        cache=cache,
    )
    assert res.interpreted_items[0].interpretation_status == "ok"
    assert len(llm.calls) == 1
