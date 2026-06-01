from __future__ import annotations
import json
from src.core.types import (ScoredItem, InterpretedItem, Evidence, InterpretConfig)


def build_item_prompt(item: ScoredItem, template: str) -> str:
    """Render the per-item prompt by substituting {{name}} placeholders.
    Double-brace placeholders avoid clashing with JSON braces in the template."""
    related = "\n".join(item.related_links)
    repl = {
        "{{title_en}}": item.title_en,
        "{{source}}": item.source,
        "{{source_type}}": item.source_type.value,
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


def build_ok_item(parsed: dict, item: ScoredItem,
                  config: InterpretConfig) -> InterpretedItem:
    """Enforce field constraints (spec §5.2) and build an 'ok' InterpretedItem.
    Raises ValueError if tags count != config.tags_count (caller falls back)."""
    tags = parsed.get("tags")
    if not isinstance(tags, list) or len(tags) != config.tags_count:
        raise ValueError("tags count not met")
    title = str(parsed.get("title", ""))[:config.title_max_chars]
    summary = str(parsed.get("summary", ""))[:config.summary_max_chars]
    takeaway = str(parsed.get("takeaway", ""))
    hot_take = str(parsed.get("hot_take", ""))
    evidence = _filter_evidence(parsed.get("evidence"), item)
    eligible = bool(takeaway) and len(evidence) >= config.min_evidence
    return InterpretedItem(
        **item.model_dump(), title=title, summary=summary, takeaway=takeaway,
        hot_take=hot_take, tags=[str(t) for t in tags], evidence=evidence,
        interpretation_status="ok", eligible_for_must_read=eligible)


def extractive_fallback(item: ScoredItem,
                        config: InterpretConfig) -> InterpretedItem:
    """No-fabrication fallback (spec §5.3): keep title_en, truncate raw_summary,
    leave generated fields empty, mark ineligible for must-read."""
    return InterpretedItem(
        **item.model_dump(), title=item.title_en,
        summary=(item.raw_summary or "")[:config.summary_max_chars],
        takeaway="", hot_take="", tags=[], evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False)
