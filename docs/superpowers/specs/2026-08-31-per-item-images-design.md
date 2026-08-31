# 逐条配图设计（2026-08-31）

> 源头：用户要把日报定点发到公众号和其他平台，参考自己过去的 AI 周报（`neverbiasu.github.io/src/zh/posts/ai-weekly`）——那里每条都有一张配图（论文 teaser、pipeline 图、产品截图）。现状是一张图都没有：九条等密度的纯文字段落，手机上就是九块灰色色块，读者没有落点。

## 目标

给最终发布的每个条目配一张**来自原文的**图，抓不到就留白。图只用于发布渲染，不进审阅卡片。

## 不做什么

- **不做图片托管/CDN**。只登记 URL，不下载、不转存、不改尺寸。（公众号那边的转存由微信编辑器负责，见下方未决问题。）
- **不给审阅卡片配图**。TG 卡片保持现状，一条一张卡、纯文字。
- **不做通用爬虫**。只按 source 类型走几条确定的抽取规则，抓不到就算了。
- **不做人脸/版权识别**。取而代之的是按源做白名单（见 §3）。

## 1. 实测依据

2026-08-30 拿当天真实发卡池的链接逐个探测（21 个源各取一条）。

**og:image 覆盖 18/21**，但质量分三档：

| 档 | 源 | 实际拿到的东西 |
|---|---|---|
| 有价值 | `aws-ml` / `openai` / `the-decoder` / `roboflow` / `comfy` / 各新闻站 | 真实文章配图、截图 |
| 有价值 | `kling-yt` / `deepgram-yt` | `i.ytimg.com/.../maxresdefault.jpg` 视频缩略图 |
| **没价值** | 全部 GitHub 源（6/21） | `opengraph.githubassets.com/...` 自动生成的"仓库名+描述+头像"模板卡，每条同构 |
| **没价值** | `hf-papers` | `cdn-thumbnails.huggingface.co/social-thumbnails/papers/<id>/gradient.png`——三篇论文拿到三张**完全相同的渐变占位图** |
| 抓不到 | `pytorch`（无 og 标签）、YouTube Shorts、`marktechpost`（Cloudflare 403） | — |

**论文必须走 arXiv HTML 首图，不能用 og:image。** 同三篇论文：

```
arXiv HTML 首个 <img>  →  gigabrain07_teaser_compressed.png
                          figs/teaser/temporal_before.jpg
                          main_figure_revised.png
```

3/3 命中真实 teaser 图，正是参考周报里用的那种。URL 形如 `https://arxiv.org/html/<arxiv_id>v1/<src>`，已验证可访问：`200 image/png 1635083 bytes`。

> 注意：`<img src>` 是相对 `https://arxiv.org/html/<id>v1/` 的，拼接时不要重复 id 段——实测重复段返回 404。
> 注意：1.6MB 偏大，公众号有素材大小限制，见未决问题。

## 2. 架构

完全复用 `hf_readme` 那条已有的富化链路，不新造机制：

```
src/adapters/enrich/item_image.py   ItemImageClient.fetch(url) -> str | None   （唯一 IO）
src/pipeline/item_image.py          enrich_item_images(items, client, config, ctx)  （编排 + 事件）
config/enrich.yaml                  item_image: 配置块
src/cli.py                          _collect_then_enrich 里一行
src/core/types.py                   NewsItem.image_url: str | None = None
```

`image_url` 加在 `NewsItem` 上，沿用 `story_id`/`cluster_id` 的 pydantic 继承透传，下游各层不需要手动搬运。

**抽取策略是纯函数**（`extract_image(html, url) -> str | None`），网络隔离在 adapter 里，可离线单测。

### 运行位置：发布前，不是采集时

跟 `hf_readme` 不同，这一步**只对最终要发布的条目跑**，不对 50 条发卡池跑：

- 发卡池 50 条里最终只发 ~9 条，对全池抓图是 5 倍浪费。
- 配图只影响渲染，审阅阶段不需要。

所以位置在 `publish.py::build_report()` 拿到最终条目之后、渲染之前，或作为 `finalize` tick 里的独立一步。**这一点实现时需要确认**：`build_report` 目前是纯函数，塞网络 IO 进去会破坏它的可测性——更可能的做法是在 `run_finalize_tick` 里、调 `build_report` 之前对已定稿条目富化。

## 3. 按源的抽取规则

按 `adapter` / `source` 分派，规则表放配置：

| 源 | 规则 | 理由 |
|---|---|---|
| `hf-papers` | 取 arXiv HTML 首个 `<img>` | og:image 是通用占位图；首图通常是 teaser |
| YouTube 系 | og:image（即 `maxresdefault.jpg`） | 缩略图本身就是作者选的封面 |
| 一手官方博客（openai / aws-ml / pytorch / deepmind…） | og:image | 发布方自己的配图，权利关系最清楚 |
| `github_releases` / `github_trending` | **不配图** | 模板卡零信息量，留白比放它好 |
| 新闻媒体（techcrunch / verge / wired / ars…） | **默认不配图** | 见下方版权 |
| 其余 | og:image 兜底 | — |

### 版权：新闻媒体的图默认不取

TechCrunch / The Verge / Wired 的 og:image 是**有版权的新闻摄影**，扒进公众号发布跟"抓不到图"是两个量级的问题——后者是缺陷，前者是风险。arXiv 图、GitHub/HF 内容、以及发布方自己博客的配图，权利关系清楚得多。

默认关闭，配置里留开关，由用户决定要不要开。

## 4. GitHub 条目：留白 vs 生成图

用户提出可以用 Gemini/ChatGPT 按描述生成配图。这条**记录下来但不推荐先做**，理由是它跟本仓库的核心纪律直接冲突：

> CLAUDE.md：`LLM 一律结构化 JSON 输出 + schema 校验；解析失败回退抽取式，宁可少写不可编造。`
> `关键事实必须带 evidence（原文锚点）；无证据不进"今日必读"。`

**生成的配图是一条没有锚点的视觉断言。** 文字层面我们花了三个 PR（#102、#115、#117）去防 LLM 编造实体名；在图像层面引入一个凭描述臆造的画面，是把刚堵上的口子换个形式重新打开——而且图比文字更难被读者识破为"这是 AI 编的"。一张想象出来的产品界面截图，比一句错误的公司名危害大。

如果之后仍要做，前置条件建议写死：
- 只生成**抽象/装饰性**画面，绝不生成产品界面、图表、人物、或任何看起来像"实拍/实截"的东西；
- 图上或图注注明由 AI 生成；
- 成本按条计费，需要先确认 provider（agnes 是 OpenAI 兼容 chat 网关，是否提供图像端点未验证）。

**本 spec 的选择：GitHub 条目留白。** 九条里有两三条没有图，视觉上不成问题；参考周报里图的价值来自它是**真实的 teaser/pipeline 图**，不是"有一张图"这件事本身。

## 5. 失败处理

- 抓取失败 / 超时 / 无匹配 → `image_url = None`，**条目照常发布**，绝不因为没图而丢条目或阻塞。
- 逐条 try/except 隔离，一条失败不影响其它条（同 `interpret` 的 per-item 隔离）。
- 并发抓取，带超时上限（参考 `hf_readme` 的 `concurrency: 5` / `timeout_s: 8`）。
- 每步写事件：`item_image_start` / `item_image_done{attempted, found, failed}`。**`found` 和 `failed` 必须分开**——否则"这批源都没图"和"抓取全挂了"在日志上同形，这是 #119 storylink 已经踩过的坑。

## 6. 渲染

- **公众号 md**：条目标题下方一行 `![](url)`。
- **网站**：同样位置插入，Hugo 侧不需要改模板。
- `image_url` 为空时不输出任何占位符，不留空行。

## 7. 配置

```yaml
item_image:
  enabled: true
  timeout_s: 8
  concurrency: 5
  skip_adapters: ["github_releases", "github_trending"]   # 模板卡, 留白更好
  allow_news_media: false                                  # 版权, 默认关
  max_bytes: 2000000                                       # 超大图跳过(实测 arXiv 有 1.6MB)
```

阈值全部读配置，不写死（CLAUDE.md）。

## 8. 测试要点

- `extract_image` 纯函数：og:image 两种属性顺序（`property` 在前 / `content` 在前）、无 og 标签、arXiv 相对路径拼接（**含"不得重复 id 段"的回归用例**，这是实测踩到的 404）。
- 按源分派：`github_releases` 一律返回 None；新闻源在 `allow_news_media: false` 时返回 None。
- 富化编排：抓取异常 → 该条 `image_url=None` 且其余条目不受影响；全部失败时条目数不变。
- 渲染：有图输出 `![](url)`、无图不输出空行。
- 事件：`found` / `failed` 分别计数。

## 未决问题（实现前必须先答）

1. **公众号能不能显示外链图片？** 公众号正文的图必须托管在微信自己的服务器上，外链一般不显示。粘贴富文本进公众号编辑器时微信**通常**会自动抓取转存，但这条链路（doocs/md → 公众号编辑器）没有验证过。**这个如果不成立，本 spec 对公众号渠道就是白做**，只对网站有效。需要用户用一张外链图实测一次。
2. **微信素材的大小/格式限制**，决定 `max_bytes` 取值，以及 1.6MB 的 arXiv 图要不要压。
3. **富化插在哪一步**：`build_report()` 目前是纯函数，不宜塞 IO；倾向放在 `run_finalize_tick` 里、`build_report` 之前。实现时定。

## 相关

- 依赖的富化模式：`src/pipeline/hf_readme.py`（2026-07-27，#85）
- 事件可观测性的同类教训：#119 storylink（fail-closed 与"没候选"同形）
- 版式与文风的上位讨论：公众号版式设计（标题/摘要/加粗/标签，尚未成文）
