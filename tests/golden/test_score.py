import logging
from datetime import datetime, timedelta, timezone

from src.core.config import load_scoring_config
from src.core.types import Genre, NewsItem, RunContext, ScoringConfig
from src.pipeline.score import compute_scores, score
from tests.fakes import DEFAULT_PUBLISHER

NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-score"))


def _ni(title, link, source, st, published=NOW):
    return NewsItem(
        title_en=title,
        link=link,
        source=source,
        genre=st,
        publisher=DEFAULT_PUBLISHER[st],
        published_at=published,
        cluster_id="evt-x",
    )


def _cfg():
    return load_scoring_config("tests/golden/data/scoring_golden.yaml")


# Case 1: selected_items = 发卡候选池(按 score top-N), 不再 per-genre 配额
def test_score_selected_is_card_pool_top_n():
    cfg = _cfg()
    cfg.card_pool_limit = 2
    items = [
        _ni("p1", "https://p/1", "s1", Genre.paper, NOW),
        _ni("p2", "https://p/2", "s2", Genre.paper, NOW - timedelta(hours=36)),
        _ni("p3", "https://p/3", "s3", Genre.paper, NOW - timedelta(hours=100)),
    ]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 2
    assert len(res.all_scored) == 3
    assert res.selected_items == res.all_scored[:2]  # all_scored 已按 score 降序
    assert res.quota_report == {}


# Case 2: 候选池未满 -> 全留, 不编造
def test_score_card_pool_keeps_all_when_under_limit():
    cfg = _cfg()
    cfg.card_pool_limit = 25
    items = [_ni("t", "https://t/1", "t1", Genre.writeup, NOW)]
    res = score(items, cfg, _ctx())
    assert res.selected_count == 1
    assert res.selected_items == res.all_scored


# Case 2b (2026-09-02 恢复): card_pool_reserved_quota 保底低分 adapter 也能进发卡池
def test_score_card_pool_reserved_quota_guarantees_adapter_slots():
    """回归: 纯按分数硬切 top-N 时, 一个类目分数上限较低但候选量大的 adapter
    (如 X)会被高分类目的长尾整体挤出发卡池, 即使它自己的中位分并不差。"""
    cfg = _cfg()
    cfg.card_pool_limit = 2
    cfg.card_pool_reserved_quota = {"x_list": 1}
    items = [
        _ni("p1", "https://p/1", "s1", Genre.paper, NOW),  # 高分, 挤满两个名额里的一个
        _ni("p2", "https://p/2", "s2", Genre.paper, NOW - timedelta(hours=1)),  # 高分
        _ni("x1", "https://x/1", "s3", Genre.announcement, NOW - timedelta(hours=2)).model_copy(
            update={"adapter": "x_list"}
        ),  # 分数最低, 纯硬切下会被砍掉
    ]
    res = score(items, cfg, _ctx())
    links = {s.link for s in res.selected_items}
    assert "https://x/1" in links  # 保底生效, 没被砍
    assert res.selected_count == 2  # card_pool_limit 仍然是硬上限, 不因保底而放宽


def test_score_card_pool_reserved_quota_empty_is_noop():
    cfg = _cfg()
    cfg.card_pool_limit = 2
    items = [
        _ni("p1", "https://p/1", "s1", Genre.paper, NOW),
        _ni("p2", "https://p/2", "s2", Genre.paper, NOW - timedelta(hours=1)),
        _ni("p3", "https://p/3", "s3", Genre.paper, NOW - timedelta(hours=2)),
    ]
    res = score(items, cfg, _ctx())
    assert res.selected_items == res.all_scored[:2]  # 行为跟恢复前完全一致


# Case 3 (spec §9.3): recency bands
def test_golden_recency_bands():
    items = [
        _ni("fresh", "https://o/1", "s1", Genre.announcement, NOW),
        _ni("mid", "https://o/2", "s2", Genre.announcement, NOW - timedelta(hours=36)),
        _ni("zero", "https://o/3", "s3", Genre.announcement, NOW - timedelta(hours=60)),
        _ni("stale", "https://o/4", "s4", Genre.announcement, NOW - timedelta(hours=100)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    band = {s.link: s.score_breakdown["时效"] for s in scored}
    assert band["https://o/1"] == 10
    assert band["https://o/2"] == 4
    assert band["https://o/3"] == 0
    assert band["https://o/4"] == -10


# Case 4 (spec §9.4): same-source penalty by published order
def test_golden_same_source_penalty():
    items = [
        _ni("late", "https://s/3", "dup", Genre.news, NOW - timedelta(hours=1)),
        _ni("early", "https://s/1", "dup", Genre.news, NOW - timedelta(hours=3)),
        _ni("mid", "https://s/2", "dup", Genre.news, NOW - timedelta(hours=2)),
    ]
    scored = compute_scores(items, {}, _cfg(), _ctx())
    pen = {s.link: s.score_breakdown["惩罚"] for s in scored}
    assert pen["https://s/1"] == 0  # earliest
    assert pen["https://s/2"] == -5
    assert pen["https://s/3"] == -5


# Case 5 (spec §9.5): empty input -> silent
def test_golden_empty_input_is_silent():
    res = score([], _cfg(), _ctx())
    assert res.selected_items == [] and res.all_scored == []
    assert res.input_count == 0 and res.selected_count == 0
    assert res.is_silent is True


# Case 6 (spec §9.6): determinism + clamp + breakdown sums to score
def test_golden_clamp_and_breakdown_sum_and_determinism():
    items = [_ni("a", "https://a/1", "s1", Genre.announcement, NOW)]
    # high config -> clamp to 100
    hi = ScoringConfig()
    hi.genre_value = {"announcement": {"一手性": 90, "技术价值": 90, "产业影响": 0, "扩散潜力": 0}}
    hi.publisher_authority = {"lab": 90}
    s1 = compute_scores(items, {}, hi, _ctx())
    assert s1[0].score == 100
    assert s1[0].score == max(0, min(100, round(sum(s1[0].score_breakdown.values()))))
    # low/negative config -> clamp to 0
    lo = ScoringConfig()
    lo.genre_value = {"announcement": {"一手性": -50, "技术价值": 0, "产业影响": 0, "扩散潜力": 0}}
    lo.publisher_authority = {"lab": -50}
    lo.fresh_bonus = 0
    s2 = compute_scores(items, {}, lo, _ctx())
    assert s2[0].score == 0
    # determinism: same input + same ctx -> identical scores
    again = compute_scores(items, {}, hi, _ctx())
    assert [x.score for x in s1] == [x.score for x in again]


def test_card_pool_floor_drops_low_scoring_candidates():
    """发卡池分数下限 (#167)。

    2026-09-07 实测: X 归零那天 88 张卡里最低 27 分, 28% 低于发布下限 40——那些卡
    就算用户按 keep 也必定在发布层被丢掉, 审它们是白费注意力。而正常日子(X 有产出)
    100% 的候选本来就 >=50 分, 所以低分候选只在供给差的日子才冒出来, 正是用户抱怨
    的那批二手新闻。

    取 60 的依据是真实发布数据: 09-04 实际发出的 7 条分数 82-100, 最低 82,
    下限 60 一条都不会碰到。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_min_score = 60
    items = [
        _ni("fresh", "https://p/1", "s1", Genre.paper, NOW),
        _ni("stale", "https://p/2", "s2", Genre.news, NOW - timedelta(hours=100)),
    ]
    res = score(items, cfg, _ctx())
    assert all(s.score >= 60 for s in res.selected_items)
    assert all(s.score >= 60 for s in res.selected_items), "低分候选不该进发卡池"
    # 全量打分不受影响: 下限只管进不进发卡池, 不改分数本身
    assert len(res.all_scored) == 2


def test_card_pool_floor_of_zero_keeps_everything():
    """下限 0 = 关闭这道闸, 行为与加它之前完全一致。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_min_score = 0
    items = [
        _ni("fresh", "https://p/1", "s1", Genre.paper, NOW),
        _ni("stale", "https://p/2", "s2", Genre.news, NOW - timedelta(hours=100)),
    ]
    assert len(score(items, cfg, _ctx()).selected_items) == 2


def test_card_pool_floor_defaults_to_off_for_backward_compatibility():
    from src.core.types import ScoringConfig as SC

    assert SC().card_pool_min_score == 0


def test_card_pool_caps_items_per_account():
    """单账号刷屏封顶 (#175)。

    2026-09-08 实测: @higgsfield_ai 一个账号占了 100 条发卡池里的 18 条(算上
    @higgsfield 共 21 条), 分数 73-83 且其中 13 条并列 73——高度雷同的分数正是
    同一批模板化营销贴的特征。用户看到的是"巨多各种 demo"。

    X 的 `source` 是列表名(x-ai-company)不是账号, 所以同源惩罚看不见账号;
    这里按链接里的 handle 分组, 那才是真正的发布方。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_account_cap = 2
    items = [
        _ni(f"spam{i}", f"https://x.com/spammer/status/{i}", "x-ai-product", Genre.announcement)
        for i in range(6)
    ]
    items.append(_ni("real", "https://x.com/other/status/1", "x-ai-product", Genre.announcement))
    res = score(items, cfg, _ctx())
    links = [s.link for s in res.selected_items]
    spam = [ln for ln in links if "/spammer/" in ln]
    assert len(spam) == 2, f"同一账号最多留 2 条, 实际 {len(spam)}"
    assert any("/other/" in ln for ln in links), "别的账号不该被连累"


def test_account_cap_keeps_the_highest_scoring_posts_from_that_account():
    """封顶留分数最高的那几条, 不是随机或最早的。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_account_cap = 1
    items = [
        _ni(
            "old",
            "https://x.com/acct/status/1",
            "x-ai-product",
            Genre.announcement,
            NOW - timedelta(hours=60),
        ),
        _ni("fresh", "https://x.com/acct/status/2", "x-ai-product", Genre.announcement, NOW),
    ]
    res = score(items, cfg, _ctx())
    assert len(res.selected_items) == 1
    # 时效加分让 fresh 分数更高, 所以留下的应当是它
    assert res.selected_items[0].link.endswith("/2")


def test_non_x_items_are_capped_by_source():
    """非 X 条目按 source 分组封顶——一个博客一天刷十篇同样是刷屏。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_account_cap = 2
    items = [_ni(f"p{i}", f"https://blog.example/{i}", "someblog", Genre.writeup) for i in range(5)]
    res = score(items, cfg, _ctx())
    assert len(res.selected_items) == 2


def test_account_cap_of_zero_is_off():
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.card_pool_account_cap = 0
    items = [
        _ni(f"s{i}", f"https://x.com/acct/status/{i}", "x-ai-product", Genre.announcement)
        for i in range(5)
    ]
    assert len(score(items, cfg, _ctx()).selected_items) == 5


def test_same_source_penalty_now_applies_per_x_account():
    """键修对之后, 同源惩罚**能够**按账号生效——这条测试把机制钉住。

    生产配置仍然豁免 x_list, 是刻意保留的(见 config/scoring.yaml 旁边的说明):
    撤销收益很小(固定 -5, 73-83 分只降到 68-78, 仍在发卡下限之上), 代价却是
    2026-07-25 记录过的那件事——一个账号一天真发三件不同的事, 第 2、3 条被无谓降权。
    刷屏由 card_pool_account_cap 和按账号的 quality_weight 解决, 不靠这个惩罚。
    这里在测试内部清空豁免, 所以将来若要改配置, 机制已经有覆盖 (#175)。"""
    cfg = _cfg()
    cfg.card_pool_limit = 100
    cfg.same_source_penalty_exempt_adapters = []
    a1 = _ni("a1", "https://x.com/spammer/status/1", "x-ai-product", Genre.announcement, NOW)
    a2 = _ni(
        "a2",
        "https://x.com/spammer/status/2",
        "x-ai-product",
        Genre.announcement,
        NOW - timedelta(hours=1),
    )
    other = _ni("o", "https://x.com/other/status/9", "x-ai-product", Genre.announcement, NOW)
    scored = {s.link: s for s in score([a1, a2, other], cfg, _ctx()).all_scored}
    pen = {k: v.score_breakdown.get("惩罚", 0) for k, v in scored.items()}
    assert pen["https://x.com/spammer/status/1"] < 0, "同账号第二条应当吃到同源惩罚"
    assert pen["https://x.com/other/status/9"] == 0, "别的账号不该被连累"
