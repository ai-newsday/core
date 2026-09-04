"""逐条配图的抽取规则(spec 2026-08-31-per-item-images-design)。
全部是纯函数, 不走网络。"""

from src.pipeline.item_image import (
    extract_image,
    extract_image_candidates,
    image_source_for,
)

OG_PROPERTY_FIRST = """
<html><head>
<meta property="og:image" content="https://cdn.example.com/a.png">
</head></html>
"""

OG_CONTENT_FIRST = """
<html><head>
<meta content="https://cdn.example.com/b.png" property="og:image">
</head></html>
"""

OG_NAME_ATTR = """
<html><head>
<meta name="og:image" content="https://cdn.example.com/c.png">
</head></html>
"""

NO_OG = "<html><head><title>nothing here</title></head></html>"

# arXiv HTML: src 是相对 https://arxiv.org/html/<id>v1/ 的
ARXIV_HTML = """
<html><body>
<figure><img src="gigabrain07_teaser_compressed.png" alt="teaser"></figure>
<figure><img src="figs/second.png"></figure>
</body></html>
"""

ARXIV_HTML_NESTED = """
<html><body><img src="figs/teaser/temporal_before.jpg"></body></html>
"""


def test_og_image_property_before_content():
    assert extract_image(OG_PROPERTY_FIRST, "https://x/1") == "https://cdn.example.com/a.png"


def test_og_image_content_before_property():
    assert extract_image(OG_CONTENT_FIRST, "https://x/1") == "https://cdn.example.com/b.png"


def test_og_image_accepts_name_attribute():
    assert extract_image(OG_NAME_ATTR, "https://x/1") == "https://cdn.example.com/c.png"


def test_no_og_image_returns_none():
    assert extract_image(NO_OG, "https://x/1") is None


def test_arxiv_takes_first_figure_not_og():
    """论文的 og:image 是通用渐变占位图(三篇论文同一张), 必须走首图。"""
    url = "https://arxiv.org/html/2608.15875v1"
    got = extract_image(ARXIV_HTML, url)
    assert got == "https://arxiv.org/html/2608.15875v1/gigabrain07_teaser_compressed.png"


def test_arxiv_does_not_repeat_the_id_segment():
    """回归: 2026-08-31 实测拼出 .../2608.15875v1/2608.15875v1/x.png 返回 404,
    正确的 .../2608.15875v1/x.png 返回 200 image/png。"""
    url = "https://arxiv.org/html/2608.15875v1"
    got = extract_image(ARXIV_HTML, url)
    assert got is not None
    assert got.count("2608.15875v1") == 1


def test_arxiv_keeps_nested_relative_path():
    url = "https://arxiv.org/html/2608.20492v1"
    got = extract_image(ARXIV_HTML_NESTED, url)
    assert got == "https://arxiv.org/html/2608.20492v1/figs/teaser/temporal_before.jpg"


ARXIV_HTML_SRC_HAS_ID = """
<html><body><img src="2608.28281v1/looparena_harness.png"></body></html>
"""

ARXIV_HTML_LOGO_FIRST = """
<html><body>
<img src="https://arxiv.org/static/base/1.0.1/images/funders/simons-foundation.png">
<img src="real_teaser.png">
</body></html>
"""


def test_arxiv_src_already_containing_the_id_yields_both_candidates():
    """2026-09-01 实测: arXiv 的 src 有两种形式, 只按一种拼会 404。
    2608.15875 给的是纯文件名, 2608.28281 给的已含 `<id>v1/` 前缀——后者按前者
    的规则拼会得到 .../2608.28281v1/2608.28281v1/x.png, 实测 404。
    抽取要产出两个候选, 由调用方按"能不能真打开"择一。"""
    url = "https://arxiv.org/html/2608.28281v1"
    cands = extract_image_candidates(ARXIV_HTML_SRC_HAS_ID, url)
    assert "https://arxiv.org/html/2608.28281v1/looparena_harness.png" in cands
    assert all(c.count("2608.28281v1") <= 1 for c in cands), cands


def test_arxiv_plain_filename_still_resolves_against_the_paper_dir():
    url = "https://arxiv.org/html/2608.15875v1"
    cands = extract_image_candidates(ARXIV_HTML, url)
    assert cands[0] == "https://arxiv.org/html/2608.15875v1/gigabrain07_teaser_compressed.png"


def test_arxiv_skips_funder_logos():
    """2026-09-01 实测: 没有 HTML 版的论文会落到别的页面, 首图抓成
    arxiv.org/static/.../funders/simons-foundation.png —— HTTP 200、是合法图片、
    完全没用。要跳过 /static/ 和文件名含 logo 的候选。"""
    url = "https://arxiv.org/html/2608.27370v1"
    cands = extract_image_candidates(ARXIV_HTML_LOGO_FIRST, url)
    assert cands, "应当跳过 logo 继续找下一张"
    assert all("simons-foundation" not in c for c in cands)
    assert any(c.endswith("real_teaser.png") for c in cands)


def test_no_figure_yields_no_candidates():
    assert extract_image_candidates(NO_OG, "https://arxiv.org/html/2608.1v1") == []


def test_github_items_get_no_image():
    """GitHub 的 og:image 是自动生成的仓库卡片, 每条同构、零信息量——留白更好。"""
    assert image_source_for("github_releases") is None
    assert image_source_for("github_trending") is None


def test_papers_dispatch_to_arxiv():
    assert image_source_for("hf_papers") == "arxiv"


def test_unknown_adapter_falls_back_to_og():
    assert image_source_for("some_blog_rss") == "og"


# 2026-09-04 生产实测的真实 arXiv 标记(2609.02749v1, DisCo)。作者邮箱旁边有个
# 14x14 的仓库图标, 排在真插图前面 —— 名字里既没有 "logo" 也不在 /static/ 下,
# 名字黑名单挡不住; 体积也挡不住(实测 101KB, 比同批真插图 dart_fig2.png 的 38KB
# 还大)。能区分的是标签自带的尺寸: 14 对 476。
ARXIV_HTML_TINY_ICON_FIRST = """
<html><body>
<p>Correspondence: someone@example.edu
<img src="2609.02749v1/figures/repo-mark.png" id="p1.g1" class="ltx_graphics"
     style="aspect-ratio:14/14;" width="14" height="14" alt="[Uncaptioned image]"></p>
<figure id="S0.F1" class="ltx_figure">
<img src="2609.02749v1/intro.png" id="S0.F1.g1" class="ltx_graphics"
     style="aspect-ratio:476/184;" width="476" height="184" alt="Refer to caption">
</figure>
</body></html>
"""

ARXIV_HTML_NO_DIMENSIONS = """
<html><body><figure><img src="teaser.png" alt="Refer to caption"></figure></body></html>
"""


def test_arxiv_skips_tiny_inline_icons():
    """回归(2026-09-04 生产): DisCo 那条配图配成了论文里 14x14 的仓库图标。"""
    url = "https://arxiv.org/html/2609.02749v1"
    cands = extract_image_candidates(ARXIV_HTML_TINY_ICON_FIRST, url)
    assert cands, "跳过图标后应当继续找到真插图"
    assert all("repo-mark" not in c for c in cands)
    assert any(c.endswith("intro.png") for c in cands)


def test_arxiv_keeps_images_that_declare_no_size():
    """没有 width/height 属性时无从判断——放行, 别为了挡图标反而丢掉真插图。"""
    cands = extract_image_candidates(ARXIV_HTML_NO_DIMENSIONS, "https://arxiv.org/html/2608.1v1")
    assert any(c.endswith("teaser.png") for c in cands)


HF_MODEL_SOCIAL_CARD = """
<html><head><meta property="og:image"
 content="https://cdn-thumbnails.huggingface.co/social-thumbnails/models/IFM/K2-Horizon.png">
</head></html>
"""


def test_hf_model_social_thumbnail_is_rejected():
    """HF 模型页的 og:image 是自动生成的模板卡: 渐变底 + 把模型名当图片文字重复一遍,
    而模型名就是它正上方的标题。跟 spec 已拒掉的 GitHub 仓库卡片同类, 留白更好。
    (2026-09-04 查过 README 兜底: 三个真实模型里只有一个有真图, 一个没有图,
    一个只有 shields.io 徽章 —— 抓 README 反而会配出徽章, 不做。)"""
    assert extract_image(HF_MODEL_SOCIAL_CARD, "https://huggingface.co/IFM/K2-Horizon") is None


def test_ordinary_og_image_still_accepted():
    """挡的是模板卡, 不是所有 og:image。"""
    assert extract_image(OG_PROPERTY_FIRST, "https://blog.example.com/post") == (
        "https://cdn.example.com/a.png"
    )
