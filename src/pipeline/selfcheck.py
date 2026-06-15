from __future__ import annotations

from src.core.types import InterpretedItem, QualityFlag, SelfCheckConfig


def format_lint(item: InterpretedItem, config: SelfCheckConfig) -> list[QualityFlag]:
    """Deterministic format-lock report (spec §5.2). Reports only, never modifies."""
    flags: list[QualityFlag] = []

    def warn(field: str, message: str) -> None:
        flags.append(
            QualityFlag(code="format_lock", severity="warn", field=field, message=message)
        )

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
