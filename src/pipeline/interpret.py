from __future__ import annotations

import json

from src.core.prompts import load_prompt
from src.core.types import (
    Evidence,
    InterpretConfig,
    InterpretedItem,
    InterpretResult,
    QualityFlag,
    RunContext,
    ScoredItem,
)
from src.observability.events import emit

# x_list 抓的是一个 X List(聚合多个真实账号), source 是内部 List slug 不是单一公司名
# (如 "x-ai-company"), 直接喂给 LLM 当"来源"曾被联想成撞名的真实公司 "xAI" 编造进日报。
_AGGREGATE_SOURCE_ADAPTERS = {"x_list"}
_AGGREGATE_SOURCE_LABEL = (
    "多账号聚合列表(不是单一公司/机构名);真实发布方以原文摘要里的 @handle 为准"
)


def build_item_prompt(item: ScoredItem, template: str, config: InterpretConfig) -> str:
    """Render the per-item prompt by substituting {{name}} placeholders.
    Double-brace placeholders avoid clashing with JSON braces in the template.
    raw_summary is capped at config.raw_summary_max_chars so an oversized
    changelog/release body can't blow the LLM's prompt budget and force a
    fallback (spec §1)."""
    related = "\n".join(item.related_links)
    raw_summary = _trim_to_sentence(item.raw_summary or "", config.raw_summary_max_chars)
    source_field = (
        _AGGREGATE_SOURCE_LABEL if item.adapter in _AGGREGATE_SOURCE_ADAPTERS else item.source
    )
    repl = {
        "{{title_en}}": item.title_en,
        "{{source}}": source_field,
        "{{genre}}": item.genre.value,
        "{{link}}": item.link,
        "{{related_links}}": related,
        "{{raw_summary}}": raw_summary,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def parse_and_validate(raw: str) -> dict:
    """Parse a JSON object string. Raises ValueError on invalid/non-object JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON LLM output: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    return data


def _filter_evidence(raw_evidence, item: ScoredItem) -> list[Evidence]:
    allowed = {item.link, *item.related_links}
    out: list[Evidence] = []
    for e in raw_evidence or []:
        if not isinstance(e, dict):
            continue
        claim = str(e.get("claim", "")).strip()
        anchor = str(e.get("anchor", "")).strip()
        if claim and anchor in allowed:
            out.append(Evidence(claim=claim, anchor=anchor))
    return out


_SENT_ENDS = "。！？!?；;"


def _trim_to_sentence(text: str, n: int) -> str:
    """超长则截到上限内最后一个句末标点(含); 无标点则硬切 + 省略号。
    "." 只在其后紧跟空白或就是窗口末尾时才算句末, 避开版本号(v2.2.11-canary.3)/缩写(e.g.)。"""
    if len(text) <= n:
        return text
    window = text[:n]
    dot_cut = -1
    for i, ch in enumerate(window):
        if ch == "." and (i + 1 == len(window) or window[i + 1].isspace()):
            dot_cut = i
    cut = max([window.rfind(ch) for ch in _SENT_ENDS] + [dot_cut], default=-1)
    if cut >= 0:
        return window[: cut + 1]
    return text[: n - 1] + "…"


def build_ok_item(
    parsed: dict,
    item: ScoredItem,
    config: InterpretConfig,
    uncertain_content_penalty: float = -15.0,
) -> InterpretedItem:
    """Enforce field constraints (spec §5.2) and build an 'ok' InterpretedItem.
    Raises ValueError if tags count != config.tags_count (caller falls back)."""
    tags = parsed.get("tags")
    if not isinstance(tags, list) or len(tags) != config.tags_count:
        raise ValueError("tags count not met")
    title = str(parsed.get("title", ""))[: config.title_max_chars]
    body = _trim_to_sentence(str(parsed.get("body", "")), config.body_max_chars)
    relevant = bool(parsed.get("relevant", True))
    evidence = _filter_evidence(parsed.get("evidence"), item)
    eligible = bool(body) and len(evidence) >= config.min_evidence
    score = item.score
    breakdown = dict(item.score_breakdown)
    if not parsed.get("content_certain", True):
        score = max(0, min(100, score + round(uncertain_content_penalty)))
        breakdown["内容确定性"] = uncertain_content_penalty
    quality_flags: list[QualityFlag] = []
    if not parsed.get("entity_confident", True):
        quality_flags.append(
            QualityFlag(
                code="entity_uncertain",
                severity="warn",
                field="title",
                message="模型/公司归属未完全确定, 请核实后再发布",
            )
        )
    return InterpretedItem(
        **{**item.model_dump(), "score": score, "score_breakdown": breakdown},
        title=title,
        body=body,
        tags=[str(t) for t in tags],
        evidence=evidence,
        interpretation_status="ok",
        eligible_for_must_read=eligible,
        relevant=relevant,
        quality_flags=quality_flags,
    )


def extractive_fallback(
    item: ScoredItem, config: InterpretConfig, *, fallback_reason: str | None = None
) -> InterpretedItem:
    """No-fabrication fallback (spec §5.3): keep title_en, truncate raw_summary,
    leave generated fields empty, mark ineligible for must-read."""
    return InterpretedItem(
        **item.model_dump(),
        title=item.title_en,
        body=_trim_to_sentence(item.raw_summary or "", config.body_max_chars),
        tags=[],
        evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
        relevant=True,
        fallback_reason=fallback_reason,
    )


def interpret_item(
    item: ScoredItem,
    item_template: str,
    config: InterpretConfig,
    llm,
    logger=None,
    uncertain_content_penalty: float = -15.0,
) -> InterpretedItem:
    """One item: prompt -> LLM chain (each with parse validation) -> enforce.

    Uses ``complete_json`` with a validator so parse failure counts as that
    model failing, letting the remaining models try. Any final failure -> extractive fallback (spec §5.2/§5.3).
    Optional `logger` enables an `interpret_error` emit before fallback."""
    parsed_holder: dict = {}

    def _validate(raw: str) -> None:
        parsed_holder["parsed"] = parse_and_validate(raw)

    try:
        prompt = build_item_prompt(item, item_template, config)
        llm.complete_json(
            prompt,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            validator=_validate,
        )
        parsed = parsed_holder["parsed"]
        return build_ok_item(parsed, item, config, uncertain_content_penalty)
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "interpret_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )
        return extractive_fallback(item, config, fallback_reason=type(e).__name__)


def build_daily_prompt(items: list[InterpretedItem], template: str) -> str:
    """Render the daily-take prompt from interpreted items' titles."""
    lines = []
    for it in items:
        title = it.title if it.interpretation_status == "ok" else it.title_en
        lines.append(f"- {title}")
    return template.replace("{{items}}", "\n".join(lines))


def generate_daily_take(
    items: list[InterpretedItem], daily_template: str, config: InterpretConfig, llm, logger=None
) -> str | None:
    """One LLM call for the macro '今日看点'. Any failure -> None (no fabrication).
    Optional `logger` enables a `daily_take_error` emit on failure."""
    try:
        prompt = build_daily_prompt(items, daily_template)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        data = json.loads(raw)
        text = data.get("highlights", "") if isinstance(data, dict) else ""
        return text or None
    except Exception as e:
        if logger is not None:
            emit(logger, "daily_take_error", error_type=type(e).__name__, error=str(e)[:200])
        return None


_TITLE_MAX = 64
_DIGEST_MAX = 120
_TITLE_SUFFIX = "【AI日报】"


def plain_title(date_label: str) -> str:
    return f"AI Daily · {date_label}"


def enforce_title(title: str, date_label: str, logger=None) -> str:
    """标题必须 ≤64 字且带固定后缀; 不合规就回退成朴素标题。

    刻意不截断: 截断会切掉 `【AI日报】` 后缀, 留下半截标题, 比朴素标题更糟。

    回退时记一条 daily_title_rejected 事件, 带具体原因和被拒的原文
    (2026-09-02/03 生产两次撞见 title_generated: false 却查不出 LLM 到底
    返回了什么、卡在哪个条件——静默回退等于把这个问题焊死成永远查不出来)。"""
    t = (title or "").strip()
    if not t:
        reason = "empty"
    elif not t.endswith(_TITLE_SUFFIX):
        reason = "missing_suffix"
    elif len(t) > _TITLE_MAX:
        reason = "over_length"
    else:
        return t
    if logger is not None:
        emit(logger, "daily_title_rejected", reason=reason, length=len(t), raw=t[:120])
    return plain_title(date_label)


def enforce_digest(digest: str, logger=None) -> str:
    """摘要截到 ≤120 字的句末。

    长度必须在代码里卡: 2026-08-31 spike 实测, prompt 明写"必须 ≤120 字",
    模型仍产出 145 字——prompt 约不住长度。"""
    d = (digest or "").strip()
    out = _trim_to_sentence(d, _DIGEST_MAX)
    if not out and d and logger is not None:
        emit(logger, "daily_digest_rejected", reason="empty_after_trim", raw=d[:120])
    return out


def generate_daily_head(
    items: list[InterpretedItem],
    daily_template: str,
    config: InterpretConfig,
    llm,
    date_label: str,
    logger=None,
) -> tuple[str, str | None]:
    """一次 LLM 调用同时产出公众号标题与摘要 (spec 2026-08-31-wechat-format-design)。

    任何失败 -> (朴素标题, None), 不编造。

    标题不合规(超字数/缺后缀)时重试一次, 要求模型自己精简——2026-09-03 实测:
    "目标 3 个事件, 塞不下就退化"这条指令模型基本不会主动执行, 一次性写到
    64 字上限两倍的情况很常见; 靠 prompt 文字自觉不够, 得代码层面强制再要一次。
    重试仍不合规就老实回退, 不无限重试(成本可控: 只在第一次不合规时多花一次调用)。"""
    try:
        prompt = build_daily_prompt(items, daily_template)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("daily head output is not a JSON object")
        title = data.get("title")
        digest = data.get("digest")
        if not isinstance(title, str) or not isinstance(digest, str):
            raise ValueError("title/digest missing or not a string")

        enforced_title = enforce_title(title, date_label)
        if enforced_title == plain_title(date_label) and title.strip():
            retry_prompt = (
                prompt
                + "\n\n(上一次给出的 title 不合规——超过 64 字, 或缺少【AI日报】后缀。"
                + "请只重写 title, 精简事件数量或措辞, 严格 ≤64 字且以【AI日报】结尾；"
                + "digest 不变。仍输出同样的 JSON 结构。)"
            )
            try:
                raw2 = llm.complete_json(
                    retry_prompt, temperature=config.temperature, max_tokens=config.max_tokens
                )
                data2 = json.loads(raw2)
                if isinstance(data2, dict) and isinstance(data2.get("title"), str):
                    enforced_title = enforce_title(data2["title"], date_label, logger=logger)
                else:
                    enforced_title = enforce_title(title, date_label, logger=logger)
            except Exception:
                enforced_title = enforce_title(title, date_label, logger=logger)

        return (
            enforced_title,
            (enforce_digest(digest, logger=logger) or None),
        )
    except Exception as e:
        if logger is not None:
            emit(logger, "daily_head_error", error_type=type(e).__name__, error=str(e)[:200])
        return plain_title(date_label), None


def interpret(
    items: list[ScoredItem],
    config: InterpretConfig,
    ctx: RunContext,
    llm,
    uncertain_content_penalty: float = -15.0,
) -> InterpretResult:
    """Orchestrate per-item interpretation + daily take (spec §3, §5, §11).
    Only side effect is the injected llm; everything else is pure/testable."""
    emit(ctx.logger, "interpret_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(
            ctx.logger,
            "interpret_done",
            input_count=0,
            interpreted_count=0,
            fallback_count=0,
            silent=True,
        )
        return InterpretResult(
            interpreted_items=[],
            daily_take=None,
            input_count=0,
            interpreted_count=0,
            fallback_count=0,
            is_silent=True,
        )

    item_tpl = load_prompt(config.item_prompt_path)
    out: list[InterpretedItem] = []
    for it in items:
        res = interpret_item(
            it,
            item_tpl,
            config,
            llm,
            logger=ctx.logger,
            uncertain_content_penalty=uncertain_content_penalty,
        )
        emit(
            ctx.logger,
            "item_interpreted",
            link=res.link,
            status=res.interpretation_status,
            evidence_count=len(res.evidence),
        )
        if res.interpretation_status == "extractive_fallback":
            emit(ctx.logger, "interpret_fallback", link=res.link)
        out.append(res)

    daily_tpl = load_prompt(config.daily_prompt_path)
    date_label = ctx.now.strftime("%Y-%m-%d")
    title, daily = generate_daily_head(out, daily_tpl, config, llm, date_label, logger=ctx.logger)
    emit(
        ctx.logger,
        "daily_take_done",
        ok=daily is not None,
        title_generated=title != plain_title(date_label),
    )

    interpreted_count = sum(1 for r in out if r.interpretation_status == "ok")
    fallback_count = len(out) - interpreted_count
    emit(
        ctx.logger,
        "interpret_done",
        input_count=len(items),
        interpreted_count=interpreted_count,
        fallback_count=fallback_count,
        silent=False,
    )
    return InterpretResult(
        interpreted_items=out,
        daily_take=daily,
        wechat_title=title,
        input_count=len(items),
        interpreted_count=interpreted_count,
        fallback_count=fallback_count,
        is_silent=False,
    )


def translate_fallback_item(
    item: InterpretedItem, template: str, llm, config: InterpretConfig, logger=None
) -> None:
    """给一条 extractive_fallback 条目做纯语言翻译, 原地修改 title/body。

    只翻译不解读、不补充信息——跟其它字段(tags/evidence/interpretation_status)
    一律不动, 这条本来就是"解读失败"的产物, 翻译不该让它看起来像被解读过了。
    任何失败(LLM 报错/非 JSON/空字段)保留英文原文, 不阻塞发布——同
    generate_daily_head 的 fail-closed 原则(2026-09-02 用户要求)。"""
    if item.interpretation_status != "extractive_fallback":
        return
    try:
        prompt = template.replace("{{title_en}}", item.title_en).replace("{{body_en}}", item.body)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("translate output is not a JSON object")
        title = data.get("title")
        body = data.get("body")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(body, str)
            or not body.strip()
        ):
            raise ValueError("title/body missing or not a non-empty string")
        item.title = title.strip()[: config.title_max_chars]
        item.body = _trim_to_sentence(body.strip(), config.body_max_chars)
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "translate_fallback_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )


def translate_fallback_items(
    items: list[InterpretedItem], config: InterpretConfig, llm, logger=None
) -> None:
    """对列表里所有仍是 extractive_fallback 的条目做翻译(原地修改)。只该在最终
    发布条目上调用(如 tick.py 在 build_report() 之后), 不对发卡池全量跑——
    最终只发几条, 全池翻译是几倍浪费, 跟逐条配图(#124)同一个道理。"""
    template = load_prompt(config.translate_fallback_prompt_path)
    targets = [it for it in items if it.interpretation_status == "extractive_fallback"]
    for item in targets:
        translate_fallback_item(item, template, llm, config, logger=logger)
    if logger is not None:
        emit(logger, "translate_fallback_done", attempted=len(targets))
