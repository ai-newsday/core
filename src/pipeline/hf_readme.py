"""hf_readme: hf-models 条目没有描述文本(adapter 只调模型列表 API, raw_summary 恒为
None)。本文件的纯文本工具函数负责把抓回来的 README 原文清洗成可用素材;
抓取 + 硬过滤编排逻辑(`enrich_hf_models_readme`)在 Task 3 加上。"""

from __future__ import annotations

import re

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _clean_readme(text: str) -> str:
    """去 YAML frontmatter + markdown 图片 + HTML 标签 + 折叠多余空行(纯函数, 无网络)。"""
    out = _FRONTMATTER_RE.sub("", text)
    out = _IMAGE_RE.sub("", out)
    out = _HTML_TAG_RE.sub("", out)
    out = _BLANK_LINES_RE.sub("\n\n", out)
    return out.strip()


def _model_id_from_link(link: str) -> str:
    """hf_models adapter 固定用 f"https://huggingface.co/{{mid}}" 构造 link, 反查 model id。"""
    return link.removeprefix("https://huggingface.co/")
