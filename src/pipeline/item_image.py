"""逐条配图: 从原文页面抽一张图 (spec docs/superpowers/specs/2026-08-31-per-item-images-design.md)。

纯函数, 不走网络——HTML 由 adapter 取好后传进来。抓不到就返回 None, 条目照常发布。

2026-08-31 拿真实发卡池 21 个源实测的结论决定了这里为什么不是"一个通用 og:image 抓取器":
- GitHub 系全部返回 opengraph.githubassets.com 的自动生成仓库卡片, 每条同构、零信息量;
- hf-papers 的 og:image 是 social-thumbnails/papers/<id>/gradient.png, 三篇不同论文
  拿到三张**完全相同**的渐变占位图, 必须改走 arXiv HTML 首图(实测 3/3 命中真 teaser 图)。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

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
