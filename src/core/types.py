from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal
import logging
from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    PAPER = "paper"
    MODEL = "model"
    TOOL = "tool"
    COMMUNITY = "community"
    OFFICIAL = "official"
    NEWS = "news"
    BLOG = "blog"


class RawItem(BaseModel):
    title_en: str = Field(min_length=1)
    link: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_type: SourceType
    published_at: datetime              # MUST be tz-aware
    raw_summary: str | None = None
    image_url: str | None = None
    fetched_via: Literal["native", "firecrawl"] = "native"

    @field_validator("published_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        return v


class SourceReport(BaseModel):
    name: str
    status: Literal["working", "failed", "empty"]
    item_count: int
    error: str | None = None
    elapsed_ms: int


class SourceSpec(BaseModel):
    name: str
    url: str
    type: SourceType
    adapter: Literal["rss", "hf_papers", "hf_models"]
    status: Literal["working", "manual", "failed"] = "working"
    priority: int = 3
    needs_firecrawl: bool = False


@dataclass
class CollectionConfig:
    sources_registry_path: str
    window_hours: int = 24
    max_window_hours: int = 36
    concurrency: int = 10
    timeout_s: int = 15
    firecrawl_enabled: bool = False


@dataclass
class RunContext:
    run_id: str
    now: datetime                       # injected for determinism; MUST be tz-aware
    logger: logging.Logger


@dataclass
class CollectionResult:
    items: list[RawItem]
    source_reports: list[SourceReport]
    is_silent: bool
