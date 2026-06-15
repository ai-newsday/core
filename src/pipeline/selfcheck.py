from __future__ import annotations

import json

from src.core.prompts import load_prompt
from src.core.types import (
    InterpretedItem,
    InterpretResult,
    QualityFlag,
    RunContext,
    SelfCheckConfig,
    SelfCheckResult,
)
from src.observability.events import emit


def format_lint(item: InterpretedItem, config: SelfCheckConfig) -> list[QualityFlag]:
    """Deterministic format-lock report (spec §5.2). Reports only, never modifies."""
    flags: list[QualityFlag] = []

    def warn(field: str, message: str) -> None:
        flags.append(QualityFlag(code="format_lock", severity="warn", field=field, message=message))

    if len(item.title) > config.title_max_chars:
        warn("title", f"标题超长(>{config.title_max_chars})")
    if len(item.summary) > config.summary_max_chars:
        warn("summary", f"摘要超长(>{config.summary_max_chars})")
    if item.interpretation_status == "ok" and len(item.tags) != config.tags_count:
        warn("tags", f"标签数应为{config.tags_count},实为{len(item.tags)}")
    allowed = {item.link, *item.related_links}
    if any(e.anchor not in allowed for e in item.evidence):
        warn("evidence", "存在非法锚点(不在 link∪related_links)")
    if item.eligible_for_must_read:
        if len(item.evidence) < config.min_evidence:
            warn("evidence", f"必读条目证据不足(<{config.min_evidence})")
        if not item.takeaway:
            warn("takeaway", "必读条目缺 takeaway")
    return flags


_FIELD_WHITELIST = {"takeaway", "summary", "hot_take", "tags", "evidence"}
_CODE_SEVERITY = {"consistency": "warn", "ai_slop": "info"}


def build_critic_prompt(item: InterpretedItem, template: str) -> str:
    """Render the critic prompt by substituting {{name}} placeholders (spec §5.3)."""
    ev = "\n".join(f"- {e.claim} @ {e.anchor}" for e in item.evidence)
    repl = {
        "{{title}}": item.title,
        "{{summary}}": item.summary,
        "{{takeaway}}": item.takeaway,
        "{{hot_take}}": item.hot_take,
        "{{title_en}}": item.title_en,
        "{{raw_summary}}": item.raw_summary or "",
        "{{evidence}}": ev,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def parse_critic(raw: str, config: SelfCheckConfig) -> list[QualityFlag]:
    """Parse critic JSON into flags (spec §5.3). Raises ValueError on bad JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON critic output: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("critic output is not a JSON object")
    flags: list[QualityFlag] = []
    for code, severity in _CODE_SEVERITY.items():
        entries = data.get(code) or []
        if not isinstance(entries, list):
            continue
        for e in entries[: config.max_flags_per_item]:
            if not isinstance(e, dict):
                continue
            msg = str(e.get("message", "")).strip()[: config.message_max_chars]
            if not msg:
                continue
            field = str(e.get("field", "")).strip()
            if field not in _FIELD_WHITELIST:
                field = "*"
            flags.append(QualityFlag(code=code, severity=severity, field=field, message=msg))
    return flags


def check_item(
    item: InterpretedItem,
    template: str,
    config: SelfCheckConfig,
    llm,
    logger=None,
) -> tuple[list[QualityFlag], bool]:
    """Per-item flags = format_lint + critic. Critic only matters for eligible items.
    Returns (flags, llm_errored: bool). Never raises (advisor)."""
    flags = format_lint(item, config)
    if not item.eligible_for_must_read:
        return flags, False
    try:
        prompt = build_critic_prompt(item, template)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens
        )
        flags = flags + parse_critic(raw, config)
        return flags, False
    except Exception as e:
        if logger is not None:
            emit(
                logger,
                "selfcheck_error",
                link=item.link,
                error_type=type(e).__name__,
                error=str(e)[:200],
            )
        return flags, True


def self_check(
    result: InterpretResult, config: SelfCheckConfig, ctx: RunContext, llm
) -> SelfCheckResult:
    """Advisor pass (spec §3, §5). Attaches quality_flags; never gates/drops/edits."""
    emit(ctx.logger, "selfcheck_start", run_id=ctx.run_id, input_count=result.input_count)
    if result.is_silent or not result.interpreted_items:
        emit(
            ctx.logger,
            "selfcheck_done",
            checked_count=0,
            flagged_count=0,
            flag_count_by_code={},
            llm_error_count=0,
            silent=True,
        )
        return SelfCheckResult(
            interpreted_items=result.interpreted_items,
            daily_take=result.daily_take,
            checked_count=0,
            flagged_count=0,
            flag_count_by_code={},
            llm_error_count=0,
            is_silent=result.is_silent,
        )

    template = load_prompt(config.prompt_path)
    out: list[InterpretedItem] = []
    checked = flagged = errors = 0
    by_code: dict[str, int] = {}
    for item in result.interpreted_items:
        flags, errored = check_item(item, template, config, llm, logger=ctx.logger)
        if item.eligible_for_must_read:
            checked += 1
        if errored:
            errors += 1
        if flags:
            flagged += 1
        for f in flags:
            by_code[f.code] = by_code.get(f.code, 0) + 1
        annotated = item.model_copy(update={"quality_flags": flags})
        emit(
            ctx.logger,
            "item_self_checked",
            link=item.link,
            flag_codes=[f.code for f in flags],
            n_flags=len(flags),
        )
        out.append(annotated)

    emit(
        ctx.logger,
        "selfcheck_done",
        checked_count=checked,
        flagged_count=flagged,
        flag_count_by_code=by_code,
        llm_error_count=errors,
        silent=False,
    )
    return SelfCheckResult(
        interpreted_items=out,
        daily_take=result.daily_take,
        checked_count=checked,
        flagged_count=flagged,
        flag_count_by_code=by_code,
        llm_error_count=errors,
        is_silent=False,
    )
