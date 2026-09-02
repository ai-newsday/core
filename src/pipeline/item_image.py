"""逐条配图: 从原文页面抽一张图 (spec docs/superpowers/specs/2026-08-31-per-item-images-design.md)。

纯函数, 不走网络——HTML 由 adapter 取好后传进来。抓不到就返回 None, 条目照常发布。

2026-08-31 拿真实发卡池 21 个源实测的结论决定了这里为什么不是"一个通用 og:image 抓取器":
- GitHub 系全部返回 opengraph.githubassets.com 的自动生成仓库卡片, 每条同构、零信息量;
- hf-papers 的 og:image 是 social-thumbnails/papers/<id>/gradient.png, 三篇不同论文
  拿到三张**完全相同**的渐变占位图, 必须改走 arXiv HTML 首图(实测 3/3 命中真 teaser 图)。
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from src.core.types import ItemImageConfig, RunContext, ScoredItem
from src.observability.events import emit

# og:image 的两种属性顺序都要认: content 可能在 property/name 之前或之后
_OG_ATTR_FIRST = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_CONTENT_FIRST = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.I,
)
_FIRST_IMG = re.compile(r'<img[^>]+src=["\']([^"\']+\.(?:png|jpg|jpeg))["\']', re.I)

# 留白胜过放一张模板卡片
_NO_IMAGE_ADAPTERS = {"github_releases", "github_trending"}
_ARXIV_ADAPTERS = {"hf_papers", "hf-papers"}


def image_source_for(adapter: str) -> str | None:
    """条目该走哪条抽取规则: None = 不配图, "arxiv" = 取首图, "og" = og:image。"""
    if adapter in _NO_IMAGE_ADAPTERS:
        return None
    if adapter in _ARXIV_ADAPTERS:
        return "arxiv"
    return "og"


def _og_image(html: str) -> str | None:
    m = _OG_ATTR_FIRST.search(html) or _OG_CONTENT_FIRST.search(html)
    return m.group(1) if m else None


# arXiv 自己的静态资源(赞助商 logo 之类)不是论文插图。2026-09-01 实测: 没有 HTML 版的
# 论文会落到别的页面, 首图抓成 .../static/base/1.0.1/images/funders/simons-foundation.png
# —— HTTP 200、是合法图片、完全没用。
_JUNK_IMG = ("/static/", "logo")


def extract_image_candidates(html: str, url: str) -> list[str]:
    """arXiv HTML 首个真实插图的候选绝对 URL 列表(按可信度排序), 抽不到返回 []。

    产出多个候选而不是一个, 是因为 arXiv 的 `<img src>` 有两种形式, 2026-09-01
    实测同一天的两篇论文各占一种:
      2608.15875 -> "gigabrain07_teaser.png"          (纯文件名, 相对论文目录)
      2608.28281 -> "2608.28281v1/looparena_teaser.png" (已含 <id>v1/ 前缀)
    按前者的规则拼后者会得到 .../2608.28281v1/2608.28281v1/x.png, 实测 404;
    正确形式返回 200 image/png。两种都产出, 由调用方按"能不能真打开"择一。"""
    for m in _FIRST_IMG.finditer(html):
        src = m.group(1)
        if any(j in src.lower() for j in _JUNK_IMG):
            continue
        if src.startswith(("http://", "https://")):
            return [src]
        out = [urljoin(url.rstrip("/") + "/", src)]
        alt = urljoin("https://arxiv.org/html/", src)
        if alt not in out:
            out.append(alt)
        # 去掉重复了 id 段的畸形候选(实测 404), 免得调用方白试一次
        return [c for c in out if c.count(url.rstrip("/").rsplit("/", 1)[-1]) <= 1]
    return []


def extract_image(html: str, url: str) -> str | None:
    """从页面 HTML 抽一张图的绝对 URL; 抽不到返回 None。

    arXiv HTML 页面走首个真实插图(通常是 teaser); 其余走 og:image。
    arXiv 情形只返回首选候选——需要按可达性择一时用 extract_image_candidates。"""
    if "arxiv.org/html/" in url:
        cands = extract_image_candidates(html, url)
        return cands[0] if cands else None
    return _og_image(html)


_ARXIV_ID_RE = re.compile(r"papers/([\d.]+)")


def _arxiv_html_urls(link: str) -> list[str]:
    """hf-papers 的 link 形如 https://huggingface.co/papers/<id>; 论文正文页在
    arXiv 而不是 HF——HF 页面的 og:image 是三篇共用的渐变占位图(spec 已验证)。
    版本号大多是 v1, 但有论文只发过 v2, 两个都试。"""
    m = _ARXIV_ID_RE.search(link)
    if not m:
        return []
    pid = m.group(1)
    return [f"https://arxiv.org/html/{pid}v1", f"https://arxiv.org/html/{pid}v2"]


async def _resolve_one(item: ScoredItem, client, config: ItemImageConfig, ctx: RunContext) -> None:
    """给单个条目找一张能真正打开的图, 写进 item.image_url; 找不到就保持 None。
    任何异常都不外抛——一条抓图失败绝不能连累其它条目或整个 tick。"""
    try:
        if item.adapter in config.skip_adapters:
            return
        if item.adapter in config.news_media_adapters and not config.allow_news_media:
            return
        route = image_source_for(item.adapter or "")
        if route is None:
            return

        candidates: list[str] = []
        if route == "arxiv":
            for arxiv_url in _arxiv_html_urls(item.link):
                html = await client.fetch_html(arxiv_url)
                if html:
                    candidates = extract_image_candidates(html, arxiv_url)
                    if candidates:
                        break
            # 没有 arXiv HTML 版 -> 留白, 不回退到 HF 页面的渐变占位图(spec 决定)
        else:
            html = await client.fetch_html(item.link)
            if html:
                img = _og_image(html)
                if img:
                    candidates = [img]

        for cand in candidates:
            # 找到 URL 和拿到图是两回事: 候选可能是死链或畸形拼接, 必须真的校验
            # 打得开才写进 image_url(2026-09-01 实测踩过这个坑)。
            if await client.check_image(cand):
                item.image_url = cand
                return
    except Exception as e:
        emit(
            ctx.logger,
            "item_image_error",
            link=item.link,
            error_type=type(e).__name__,
            error=str(e)[:200],
        )


async def enrich_item_images(
    items: list[ScoredItem], client, config: ItemImageConfig, ctx: RunContext
) -> list[ScoredItem]:
    """给每条最终发布条目配一张来自原文页面的图 (spec 2026-08-31-per-item-images-design)。

    只该对最终发布的条目跑(由调用方保证, 如 tick.py 在 build_report() 之后),
    不对发卡池全量跑——最终只发几条, 全池抓图是几倍浪费。

    抓不到/校验不通过 -> image_url 留 None, 条目照常发布, 绝不因为没图卡住或
    剔除条目; found/failed 分开计数, 避免"这批源本来就没图"和"抓取全挂了"
    在日志上看着一样(#119 storylink 已经踩过这个坑)。"""
    emit(ctx.logger, "item_image_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "item_image_done", found=0, failed=0)
        return items

    sem = asyncio.Semaphore(max(1, config.concurrency))

    async def _bounded(item: ScoredItem) -> None:
        async with sem:
            await _resolve_one(item, client, config, ctx)

    await asyncio.gather(*(_bounded(it) for it in items))

    found = sum(1 for it in items if it.image_url)
    emit(ctx.logger, "item_image_done", found=found, failed=len(items) - found)
    return items
