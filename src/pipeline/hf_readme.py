"""hf_readme: hf-models 条目没有描述文本(adapter 只调模型列表 API, raw_summary 恒为
None)。抓每个候选模型的 HF README 当素材, 清洗掉 frontmatter/图片/HTML 噪声后填进
raw_summary。只处理 adapter == "hf_models" 的条目; 其余原样透传。抓不到 README、
或清洗后正文短于 min_body_chars 的条目直接从返回列表剔除(不带空 body 进审阅卡池),
不是放行后指望下游过滤器兜底。"""

from __future__ import annotations

import asyncio
import html
import re

from src.core.types import HFReadmeConfig, RawItem, RunContext
from src.observability.events import emit

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TRAILING_WS_RE = re.compile(r"[ \t]+\n")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _clean_readme(text: str) -> str:
    """去 YAML frontmatter + markdown 图片 + HTML 标签/实体 + 折叠多余空行(纯函数, 无网络)。
    真实 HF README 常见徽章/logo 区块是密集的 <div>/<a>/<img> 加 &nbsp; 类实体
    (2026-07-27 用 unsloth/Kimi-K3 真实数据核实过); 光去标签不够, 还得转义实体、
    清掉标签删完后留下的纯空白行, 否则 raw_summary 里全是 "&nbsp;" 和空白噪声。"""
    out = _FRONTMATTER_RE.sub("", text)
    out = _IMAGE_RE.sub("", out)
    out = _HTML_TAG_RE.sub("", out)
    out = html.unescape(out)
    out = _TRAILING_WS_RE.sub("\n", out)
    out = _BLANK_LINES_RE.sub("\n\n", out)
    return out.strip()


def _model_id_from_link(link: str) -> str:
    """hf_models adapter 固定用 f"https://huggingface.co/{{mid}}" 构造 link, 反查 model id。"""
    return link.removeprefix("https://huggingface.co/")


async def enrich_hf_models_readme(
    items: list[RawItem], client, config: HFReadmeConfig, ctx: RunContext
) -> list[RawItem]:
    """对 adapter == "hf_models" 的条目抓 README 填 raw_summary; 其余原样透传。
    返回硬过滤后的列表(抓不到内容或内容太短的条目被剔除)。"""
    emit(ctx.logger, "hf_readme_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "hf_readme_done", fetched=0, filtered=0)
        return items

    targets = [it for it in items if it.adapter == "hf_models"]
    if not targets:
        emit(ctx.logger, "hf_readme_done", fetched=0, filtered=0)
        return items

    sem = asyncio.Semaphore(max(1, config.concurrency))
    cleaned: dict[str, str | None] = {}

    async def _fetch_one(item: RawItem) -> None:
        async with sem:
            try:
                raw = await client.fetch_readme(_model_id_from_link(item.link))
            except Exception as e:
                emit(
                    ctx.logger,
                    "hf_readme_error",
                    link=item.link,
                    error_type=type(e).__name__,
                    error=str(e)[:200],
                )
                cleaned[item.link] = None
                return
        cleaned[item.link] = _clean_readme(raw) if raw else None

    await asyncio.gather(*(_fetch_one(it) for it in targets))

    out: list[RawItem] = []
    fetched = filtered = 0
    for item in items:
        if item.adapter != "hf_models":
            out.append(item)
            continue
        text = cleaned.get(item.link)
        if not text or len(text) < config.min_body_chars:
            filtered += 1
            continue
        item.raw_summary = text
        fetched += 1
        out.append(item)

    emit(ctx.logger, "hf_readme_done", fetched=fetched, filtered=filtered)
    return out
