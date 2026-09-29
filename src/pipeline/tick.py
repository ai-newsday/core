from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from src.adapters.decisions.worker import DecisionStore
from src.core.config import load_publish_config, load_review_config
from src.core.prompts import load_prompt
from src.core.types import (
    DailyReport,
    InterpretConfig,
    InterpretedItem,
    ItemImageConfig,
    ReviewDecision,
    ScoredItem,
)
from src.notifiers import Notifier
from src.observability.events import emit
from src.pipeline.collect import check_zero_yield
from src.pipeline.interpret import generate_daily_head, translate_fallback_items
from src.pipeline.item_image import enrich_item_images
from src.pipeline.publish import build_report, publish, render
from src.pipeline.review import review
from src.state.db import Database

_ZERO_YIELD_KEY = "zero_yield_state"


def _item_id(item: InterpretedItem) -> str:
    """稳定唯一 ID: sha256(link) 前 16 字符。"""
    return hashlib.sha256(item.link.encode()).hexdigest()[:16]


def _snapshot_json(item: InterpretedItem) -> str:
    """A1: 存解读前的 ScoredItem(interpret() 的输入), finalize 按它重新解读。"""
    fields = set(ScoredItem.model_fields)
    return ScoredItem.model_validate(item.model_dump(include=fields)).model_dump_json()


async def _load_kept_from_snapshots(
    remote: dict[str, str],
    db: Database,
    reinterpret: Callable[[list[ScoredItem]], list[InterpretedItem]],
    logger: logging.Logger,
) -> tuple[list[InterpretedItem], list[ScoredItem], list[tuple[str, str, str | None]]]:
    """A1: 决策 id → 快照 → 只重新解读 keep。
    返回 (报告候选, 有快照的全部已决策条目, 跳过列表[(item_id, reason, error)])。
    drop 条目不解读, 但放进第二项: id→link 映射与反馈闭环要用(负反馈不能丢)。"""
    decided = [iid for iid, a in remote.items() if a in ("keep", "drop")]
    try:
        snaps = await db.get_snapshots(decided)
    except Exception as e:  # noqa: BLE001 - 读不出快照 = 全部 no_snapshot, 不崩
        emit(logger, "snapshot_read_error", error_type=type(e).__name__, error=str(e))
        snaps = {}
    pool = {iid: ScoredItem.model_validate_json(s) for iid, s in snaps.items()}
    keep_ids = [iid for iid in decided if remote[iid] == "keep"]
    skipped: list[tuple[str, str, str | None]] = [
        (iid, "no_snapshot", None) for iid in keep_ids if iid not in pool
    ]
    to_interpret = [pool[iid] for iid in keep_ids if iid in pool]
    kept: list[InterpretedItem] = []
    for it in reinterpret(to_interpret) if to_interpret else []:
        if it.interpretation_status != "ok":
            skipped.append((_item_id(it), "interpret_failed", it.fallback_reason))
        elif not it.relevant:
            skipped.append((_item_id(it), "irrelevant", None))
        else:
            kept.append(it)
    return kept, list(pool.values()), skipped


def regenerate_wechat_head(
    report: DailyReport,
    date_label: str,
    interpret_config: InterpretConfig,
    llm,
    ctx,
) -> DailyReport:
    """标题/摘要必须基于最终发布条目生成, 不能用配额筛选前的全量解读池
    (#139: 2026-09-02 实测标题引用了 ChatGPT Ads/Qwen3.8-Flash-Next, 但这两条
    从未出现在最终发布的六条正文里——旧实现在 interpret() 阶段就生成了标题,
    早于 build_report() 的地板/adapter 配额/故事线合并/genre 配额过滤)。

    接收调用方已经跑过 build_report() 得到的最终 report(而不是自己再跑一次
    build_report() 然后指望调用方第二次调用 publish() 时还能看到这次修改——
    故事线合并会给主条目 model_copy() 出新对象, 那样两次独立的 build_report()
    互不相干, 第一次的修改会在第二次重新构建时凭空消失)。全部条目都被过滤
    掉时原样返回, 不浪费一次 LLM 调用。"""
    if not report.item_count:
        return report
    final_items = [it for cat in report.categories for it in cat.items]
    daily_tpl = load_prompt(interpret_config.daily_prompt_path)
    title, digest = generate_daily_head(
        final_items, daily_tpl, interpret_config, llm, date_label, logger=ctx.logger
    )
    emit(
        ctx.logger,
        "wechat_head_regenerated",
        final_item_count=len(final_items),
        ok=digest is not None,
    )
    return report.model_copy(
        update={
            "wechat_title": title,
            "daily_take": digest if digest is not None else report.daily_take,
        }
    )


def select_report_items(
    items: list[InterpretedItem], decisions: dict[str, ReviewDecision]
) -> list[InterpretedItem]:
    """确认门: 报告只收显式 keep/edit 的条目, 未决策 + drop 都排除。

    实现 spec(review.md §3.4 / publish.md)一直推迟给"发布层/CLI"的"未审自动发拦截":
    review 层仍默认 keep + 标 is_pending, 真正的"未确认不发"在 finalize 这层落地。
    决策仍按 link(由 item_id 解耦匹配而来)查, 不引入日期耦合(保留 #33)。
    """
    return [
        it
        for it in items
        if (dec := decisions.get(it.link)) is not None and dec.action in ("keep", "edit")
    ]


def _genre_label(genre_value: str) -> str:
    labels = {
        "paper": "论文",
        "model": "模型",
        "announcement": "官方",
        "writeup": "博客 / 工具",
        "news": "新闻",
    }
    return labels.get(genre_value, genre_value)


def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
        "status": item.interpretation_status,
        "entity_uncertain": any(f.code == "entity_uncertain" for f in item.quality_flags),
    }


async def _alert_zero_yield(
    source_reports, adapter_of, config, db: Database, notifiers, logger
) -> None:
    """按 adapter 汇总本次产出, 连续归零到阈值就报一次 (#169)。

    整段包在 try 里: 告警本身绝不能把采集 tick 弄挂——那会把"有个源坏了"升级成
    "整条流水线坏了", 正好跟这个功能的目的相反。"""
    try:
        watched = set(config.adapters)
        if not watched:
            return
        yields = {a: 0 for a in watched}
        for r in source_reports:
            a = adapter_of.get(r.name)
            if a in watched:
                yields[a] += r.item_count
        state = json.loads(await db.get_kv(_ZERO_YIELD_KEY) or "{}")
        new_state, alerts = check_zero_yield(yields, state, config.consecutive_runs)
        await db.set_kv(_ZERO_YIELD_KEY, json.dumps(new_state))
        for adapter in alerts:
            emit(logger, "zero_yield_alert", adapter=adapter, runs=config.consecutive_runs)
            text = (
                f"⚠️ <b>{adapter}</b> 连续 {config.consecutive_runs} 次采集产出为 0。\n"
                "抓取报的是成功而不是失败, 所以日志里看不出来。"
            )
            for n in notifiers:
                try:
                    await n.send_alert(text)
                except Exception as e:  # noqa: BLE001
                    emit(logger, "zero_yield_alert_error", error=str(e))
    except Exception as e:  # noqa: BLE001
        emit(logger, "zero_yield_check_error", error=str(e))


async def run_collect_tick(
    run_id: str,
    now: datetime,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
    source_reports=None,
    zero_yield_config=None,
    adapter_of: dict[str, str] | None = None,
) -> None:
    """采集 tick: 把新候选写 DB + 推 Telegram 卡片。决策由 webhook 异步收集, finalize 时拉取。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "collect")
    emit(logger, "tick_collect_start", run_id=run_id, date=date, item_count=len(interpreted_items))
    if source_reports is not None and zero_yield_config is not None:
        await _alert_zero_yield(
            source_reports, adapter_of or {}, zero_yield_config, db, notifiers, logger
        )
    pushed = 0
    for item in interpreted_items:
        if not item.relevant:
            continue
        item_id = _item_id(item)
        await db.upsert_pending_review(
            item_id=item_id,
            run_id=run_id,
            link=item.link,
            source=item.source,
            title_en=item.title_en,
            title_zh=item.title,
            summary_zh=item.body,
            takeaway="",
            hot_take="",
            score=item.score,
            signals=item.signals,
            date=date,
        )
        try:
            await db.upsert_snapshot(item_id, date, _snapshot_json(item))
        except Exception as e:  # noqa: BLE001 - 快照失败不影响推卡
            emit(logger, "snapshot_write_error", item_id=item_id, error=str(e))
        # 只推之前没发过卡片的条目（msg_id 仍为 NULL）
        rows = await db.get_pending_reviews_for_date(date)
        row = next((r for r in rows if r["item_id"] == item_id), None)
        if row and row["msg_id"] is None:
            card = _build_card(item)
            for notifier in notifiers:
                try:
                    msg_id = await notifier.send_review_card(item_id, card)
                    if msg_id is not None:
                        await db.update_msg_id(item_id, msg_id)
                except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
                    emit(logger, "notifier_send_error", item_id=item_id, error=str(e))
            pushed += 1
    emit(logger, "tick_collect_done", run_id=run_id, pushed=pushed)


async def run_finalize_tick(
    run_id: str,
    now: datetime,
    date_label: str,
    interpreted_items: list[InterpretedItem],
    daily_take: str | None,
    db: Database,
    notifiers: list[Notifier],
    decision_store: DecisionStore | None = None,
    site_base_url: str = "",
    wechat_title: str | None = None,
    llm=None,
    interpret_config: InterpretConfig | None = None,
    image_client=None,
    item_image_config: ItemImageConfig | None = None,
    reinterpret: Callable[[list[ScoredItem]], list[InterpretedItem]] | None = None,
) -> dict:
    """定稿 tick: 读决策 → review → publish → send_final_report。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    await db.insert_run(run_id, "finalize")
    emit(logger, "tick_finalize_start", run_id=run_id, date=date)
    # 决策按 item_id 匹配(与采集日解耦)。拉取失败 = 零决策 = 空稿(2026-08-06 起不再兜底自动发)。
    decisions_raw: dict[str, str] = {}
    remote_raw: dict[str, str] = {}
    if decision_store is not None:
        try:
            remote_raw = dict(await decision_store.fetch())  # {item_id: action}
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命
            emit(
                logger,
                "decisions_fetch_error",
                run_id=run_id,
                error_type=type(e).__name__,
                error=str(e),
            )
    feedback_items: list = list(interpreted_items)
    skipped: list[tuple[str, str, str | None]] = []
    if reinterpret is not None:
        interpreted_items, feedback_items, skipped = await _load_kept_from_snapshots(
            remote_raw, db, reinterpret, logger
        )
    id_to_link = {_item_id(it): it.link for it in [*interpreted_items, *feedback_items]}
    for item_id, action in remote_raw.items():
        link = id_to_link.get(item_id)
        if link is not None and action in ("keep", "drop"):
            decisions_raw[link] = action
            await db.update_decision(item_id, action)  # 记录用, 无行则 no-op
    # KV 只留 7 天; 留一份到库里, 才能按来源看长期保留率(2026-09-17)
    if remote_raw:
        await db.record_decisions(remote_raw, ts=now.isoformat())
    decisions = {link: ReviewDecision(action=action) for link, action in decisions_raw.items()}
    from src.core.types import RunContext

    ctx = RunContext(run_id=run_id, now=now, logger=logger)
    rcfg = load_review_config("config/review.yaml")
    # 确认门: 只有显式 keep/edit 的条目进报告(2026-08-06: 去掉零决策兜底自动发——
    # 用户没审的内容不结算; decisions={} 时 select_report_items 天然返回空列表)。
    # feedback 仍吃全量(下方)。
    report_items = select_report_items(interpreted_items, decisions)
    # 已发布去重: 排除已在别的 date_label 报告里发过的条目(72h 窗口内同条目跨天复发 → 去重)。
    already = await db.already_published_elsewhere(
        [_item_id(it) for it in report_items], date_label
    )
    skipped += [
        (_item_id(it), "already_published", None) for it in report_items if _item_id(it) in already
    ]
    report_items = [it for it in report_items if _item_id(it) not in already]
    rres = review(report_items, daily_take, decisions, rcfg, ctx, wechat_title=wechat_title)
    pcfg = load_publish_config("config/publish.yaml")
    final: list = []
    if not rres.reviewed_items:
        # 空报(零决策/全砍): 走 publish() 自己的静默短路, 不必生成标题或抓图。
        pres = publish(rres, date_label, pcfg, ctx)
    else:
        report = build_report(rres, date_label, pcfg)
        final = [it for cat in report.categories for it in cat.items]
        if llm is not None and interpret_config is not None:
            # 英文回退条目纯翻译(2026-09-02 用户要求, 只在最终条目上跑, 同
            # 逐条配图一个道理); 在标题/摘要重生成之前做, 但 generate_daily_head
            # 对未解读条目本来就读 title_en 不读 title, 顺序其实不影响它。
            final_items = [it for cat in report.categories for it in cat.items]
            translate_fallback_items(final_items, interpret_config, llm, logger=ctx.logger)
            report = regenerate_wechat_head(report, date_label, interpret_config, llm, ctx)
        if image_client is not None and item_image_config is not None:
            final_items = [it for cat in report.categories for it in cat.items]
            await enrich_item_images(final_items, image_client, item_image_config, ctx)
        pres = render(report, pcfg, ctx)
    # 记录本报已发布条目(按 date_label), 供后续 tick 跨天去重。首发 label 固定。
    # 没进成品的 keep 条目逐条记原因(只记日志, 不改筛选行为)。
    final_ids = {_item_id(it) for it in final}
    final_models = {it.model for it in final}
    for it in report_items:
        if _item_id(it) not in final_ids:
            reason = "model_mismatch" if final and it.model not in final_models else "rule_cut"
            skipped.append((_item_id(it), reason, None))
    for iid, reason, err in skipped:
        emit(logger, "finalize_item_skipped", run_id=run_id, item_id=iid, reason=reason, error=err)
    skipped_by_reason = dict(Counter(reason for _, reason, _ in skipped))
    emit(
        logger,
        "finalize_summary",
        run_id=run_id,
        kept=sum(1 for a in remote_raw.values() if a == "keep"),
        published=pres.report.item_count,
        skipped_by_reason=skipped_by_reason,
    )
    await db.mark_published([_item_id(it) for it in report_items], date_label)
    summary = {
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "url": (site_base_url.rstrip("/") + "/posts/" + date_label + "/") if site_base_url else "",
    }
    # 空报(零决策/全砍)不通知: WebsiteNotifier 会无条件写文件, 空 markdown 写出来
    # 就是一篇没有 front matter 的空文件; Telegram 也不该发"今天 0 条"的噪音消息。
    if not pres.is_silent:
        for notifier in notifiers:
            try:
                await notifier.send_final_report(pres.markdown, summary, pres.wechat_markdown)
            except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
                emit(logger, "notifier_final_report_error", error=str(e))
    emit(
        logger,
        "tick_finalize_done",
        run_id=run_id,
        item_count=pres.report.item_count,
    )
    # 反馈闭环 (PRD §4.5): 派生 → 幂等入账 → 增量重算权重 → 写回。非致命。
    if not await db.has_feedback_for_run(run_id):
        from src.core.config import load_feedback_config
        from src.pipeline.feedback import derive_events, feedback

        try:
            fcfg = load_feedback_config("config/feedback.yaml")
            run_events = derive_events(feedback_items, decisions, run_id=run_id, now=now)
            await db.append_feedback_events(run_events)
            prior = await db.get_quality_weights()
            fres = feedback(run_events, prior, fcfg, ctx)
            if not fres.is_silent:
                await db.upsert_quality_weights(fres.quality_weights)
        except Exception as e:  # noqa: BLE001 - feedback persistence is non-fatal
            emit(logger, "feedback_persist_error", run_id=run_id, error=str(e))
    return {
        "run_id": run_id,
        "date_label": date_label,
        "item_count": pres.report.item_count,
        "is_pending": pres.is_pending,
        "skipped_by_reason": skipped_by_reason,
    }


def count_undecided(rows: list[dict], decisions_raw: dict[str, str]) -> int:
    """纯函数: 今天推过的卡片(rows, 每条含 item_id/link)里, 有多少条还没有远端
    keep/drop 决策。按 item_id 匹配(webhook 决策以 item_id 为键), 非 keep/drop
    的值(协议外/防御性)一律不算已决策。"""
    decided_ids = {iid for iid, action in decisions_raw.items() if action in ("keep", "drop")}
    return sum(1 for r in rows if r["item_id"] not in decided_ids)


async def run_reminder_tick(
    *,
    now: datetime,
    db: Database,
    decision_store: DecisionStore | None,
    notifiers: list[Notifier],
) -> dict:
    """22:00 提醒 tick: 数今天推过的卡片里还有多少条没审, 非零才发一条提醒消息。
    只报个数, 不列标题(简单够用); 决策拉取失败保守地把当天全部条目算作待审,
    不假装 0 条从而漏发提醒——错报"还有几条"好过错过一次真实提醒。"""
    logger = logging.getLogger("ai-newsday")
    date = now.date().isoformat()
    rows = await db.get_pending_reviews_for_date(date)
    decisions_raw: dict[str, str] = {}
    if decision_store is not None:
        try:
            decisions_raw = await decision_store.fetch()
        except Exception as e:  # noqa: BLE001 - 拉取失败非致命, 保守当全部待审
            emit(
                logger,
                "reminder_decisions_fetch_error",
                error_type=type(e).__name__,
                error=str(e),
            )
    undecided_count = count_undecided(rows, decisions_raw)
    if undecided_count > 0:
        for notifier in notifiers:
            try:
                await notifier.send_reminder(undecided_count)
            except Exception as e:  # noqa: BLE001 - notifier failure is non-fatal
                emit(logger, "notifier_reminder_error", error=str(e))
    emit(logger, "tick_reminder_done", date=date, undecided_count=undecided_count)
    return {"date": date, "undecided_count": undecided_count}
