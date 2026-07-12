from __future__ import annotations

import json

from src.core.prompts import load_prompt
from src.core.types import (
    Evidence,
    InterpretConfig,
    InterpretedItem,
    InterpretResult,
    RunContext,
    ScoredItem,
)
from src.observability.events import emit


def build_item_prompt(item: ScoredItem, template: str) -> str:
    """Render the per-item prompt by substituting {{name}} placeholders.
    Double-brace placeholders avoid clashing with JSON braces in the template."""
    related = "\n".join(item.related_links)
    repl = {
        "{{title_en}}": item.title_en,
        "{{source}}": item.source,
        "{{genre}}": item.genre.value,
        "{{link}}": item.link,
        "{{related_links}}": related,
        "{{raw_summary}}": item.raw_summary or "",
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


_SENT_ENDS = "。！？!?；;."


def _trim_to_sentence(text: str, n: int) -> str:
    """超长则截到上限内最后一个句末标点(含); 无标点则硬切 + 省略号。"""
    if len(text) <= n:
        return text
    window = text[:n]
    cut = max((window.rfind(ch) for ch in _SENT_ENDS), default=-1)
    if cut >= 0:
        return window[: cut + 1]
    return text[: n - 1] + "…"


def build_ok_item(parsed: dict, item: ScoredItem, config: InterpretConfig) -> InterpretedItem:
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
    return InterpretedItem(
        **item.model_dump(),
        title=title,
        body=body,
        tags=[str(t) for t in tags],
        evidence=evidence,
        interpretation_status="ok",
        eligible_for_must_read=eligible,
        relevant=relevant,
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
    item: ScoredItem, item_template: str, config: InterpretConfig, llm, logger=None
) -> InterpretedItem:
    """One item: prompt -> LLM chain (each with parse validation) -> enforce.

    Uses ``complete_json`` with a validator so parse failure counts as that
    model failing, letting the remaining models try. Any final failure -> extractive fallback (spec §5.2/§5.3).
    Optional `logger` enables an `interpret_error` emit before fallback."""
    parsed_holder: dict = {}

    def _validate(raw: str) -> None:
        parsed_holder["parsed"] = parse_and_validate(raw)

    try:
        prompt = build_item_prompt(item, item_template)
        llm.complete_json(
            prompt,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            validator=_validate,
        )
        parsed = parsed_holder["parsed"]
        return build_ok_item(parsed, item, config)
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


def interpret(
    items: list[ScoredItem], config: InterpretConfig, ctx: RunContext, llm
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
        res = interpret_item(it, item_tpl, config, llm, logger=ctx.logger)
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
    daily = generate_daily_take(out, daily_tpl, config, llm, logger=ctx.logger)
    emit(ctx.logger, "daily_take_done", ok=daily is not None)

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
        input_count=len(items),
        interpreted_count=interpreted_count,
        fallback_count=fallback_count,
        is_silent=False,
    )
