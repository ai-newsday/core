# 信息源总览

> 路径：`docs/sources.md`。本文是 `config/sources.yaml` 的**人类可读视图** + 扩源 backlog。
> 数据基于 2026-06-03 实跑：`data/runs/20260604-010938-publish.md` 对应 `tools/pub5.log`。
> 总数：**49 个 working / 89 个 manual / 138 个 total**。

---

## 1. 当前现状（按 type）

### official（11 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | openai | official | 2 | 2 | https://openai.com/news/rss.xml |
| ✅ | aws-ml | official | 2 | 4 | https://aws.amazon.com/blogs/machine-learning/feed |
| ✅ | nvidia | official | 2 | 4 | https://blogs.nvidia.com/feed |
| ✅ | huggingface-blog | official | 2 | 2 | https://huggingface.co/blog/feed.xml |
| · | apple-ml | official | 2 | 0 | https://machinelearning.apple.com/rss.xml |
| · | deepmind | official | 2 | 0 | https://deepmind.google/blog/rss.xml |
| · | google-research | official | 2 | 0 | https://research.google/blog/rss |
| · | cohere | official | 2 | 0 | https://cohere.com/blog/rss.xml |
| · | together-ai | official | 2 | 0 | https://www.together.ai/blog/rss.xml |
| ❌ | microsoft-ai | official | 2 | 403 | https://news.microsoft.com/source/topics/ai/feed |
| ❌ | meta-ai | official | 2 | 404 | https://ai.meta.com/blog/rss/ |
| 🅼 | anthropic | official | 1 | (无 RSS) | https://www.anthropic.com/news/rss.xml |
| 🅼 | mistral | official | 2 | (无 RSS) | https://mistral.ai/news/rss.xml |
| 🅼 | ai2 | official | 2 | (无 RSS) | https://allenai.org/news.rss |

### paper（8 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | hf-papers | paper | 1 | 1 | https://huggingface.co/api/daily_papers ⭐ 编辑精选 |
| ✅ | arxiv-cs-cl | paper | 2 | 30 (capped) | http://export.arxiv.org/rss/cs.CL |
| ✅ | arxiv-cs-lg | paper | 2 | 30 (capped) | http://export.arxiv.org/rss/cs.LG |
| ✅ | arxiv-cs-ai | paper | 2 | 30 (capped) | http://export.arxiv.org/rss/cs.AI |
| ✅ | arxiv-cs-cv | paper | 3 | 30 (capped) | http://export.arxiv.org/rss/cs.CV |
| · | bair | paper | 2 | 0 | https://bair.berkeley.edu/blog/feed.xml |
| · | nature-machine-intelligence | paper | 2 | 0 | https://www.nature.com/natmachintell.rss |
| · | stanford-ai | paper | 3 | 0 | http://ai.stanford.edu/blog/feed.xml |

### model（1 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | hf-models | model | 1 | 50 | https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50 ⚠ firehose 噪声大 |

### tool（9 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | pytorch | tool | 2 | 1 | https://pytorch.org/blog/feed.xml |
| ✅ | comfy | tool | 3 | 1 | https://blog.comfy.org/feed.xml |
| ✅ | roboflow | tool | 3 | 1 | https://blog.roboflow.com/feed |
| · | langchain | tool | 2 | 0 | https://blog.langchain.dev/rss/ |
| · | llamaindex | tool | 3 | 0 | https://www.llamaindex.ai/blog?format=rss |
| · | ollama | tool | 3 | 0 | https://ollama.com/blog/rss.xml |
| · | replicate | tool | 3 | 0 | https://replicate.com/blog/rss |
| · | civitai-education | tool | 3 | 0 | https://education.civitai.com/feed |
| 🅼 | modal | tool | 3 | (无 RSS) | https://modal.com/blog/feed.xml |

### news（8 working，本圈新增）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | techcrunch-ai | news | 2 | 13 | https://techcrunch.com/category/artificial-intelligence/feed/ |
| ✅ | theverge-ai | news | 3 | 10 | https://www.theverge.com/rss/ai-artificial-intelligence/index.xml |
| ✅ | the-decoder | news | 2 | 5 | https://the-decoder.com/feed/ |
| ✅ | ars-technica-ai | news | 2 | 4 | https://arstechnica.com/ai/feed/ |
| · | venturebeat-ai | news | 2 | 0 | https://venturebeat.com/category/ai/feed/ |
| · | mit-tech-review-ai | news | 2 | 0 | https://www.technologyreview.com/topic/artificial-intelligence/feed/ |
| · | jiqizhixin | news | 2 | 0 | https://www.jiqizhixin.com/rss |
| ❌ | qbitai | news | 2 | 403 | https://www.qbitai.com/feed |

### blog（11 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | simonwillison | blog | 2 | 5 | https://simonwillison.net/atom/everything/ |
| ✅ | garymarcus | blog | 3 | 1 | https://garymarcus.substack.com/feed |
| · | interconnects | blog | 2 | 0 (上轮 1) | https://www.interconnects.ai/feed |
| · | import-ai | blog | 2 | 0 | https://importai.substack.com/feed |
| · | sebastian-raschka | blog | 2 | 0 | https://magazine.sebastianraschka.com/feed |
| · | lilian-weng | blog | 2 | 0 | https://lilianweng.github.io/index.xml |
| · | eugene-yan | blog | 3 | 0 | https://eugeneyan.com/rss/ |
| · | minimaxir | blog | 3 | 0 | https://minimaxir.com/index.xml |
| · | gwern | blog | 3 | 0 | https://gwern.substack.com/feed |
| · | geohot | blog | 3 | 0 | https://geohot.github.io/blog/feed.xml |
| · | lcamtuf | blog | 3 | 0 | https://lcamtuf.substack.com/feed |

### community（2 working）

| status | name | type | priority | 上次产出 | URL |
|---|---|---|---|---|---|
| ✅ | latent-space | community | 2 | 1 | https://www.latent.space/feed.xml |
| · | dwarkesh | community | 3 | 0 | https://www.dwarkeshpatel.com/feed |

### manual（89, 全是 OPML 进来的 HN 杂项博客）

`hn-*` 前缀 85 个 + 上述 4 个 RSS 已死的官博（anthropic/mistral/ai2/modal）。绝大多数不是 AI 专精，**不建议直接全开**，扩源用人工挑选的新名单。

---

## 2. 失衡分析

### 2.1 arxiv 量大 ≠ 质好

- 4 个 arxiv 源 capped 后 4×30 = **120 条 paper 候选**
- 但 paper quota 只有 2，且 hf-papers（编辑精选）天然得高分
- arxiv 是 firehose：**0 引用、0 star、0 编辑信号**，标题学术化，对日报读者不友好
- **本轮 8 条入选，arxiv 仅 1 条进**（IdiomX 多语言成语），且评分 78 排第 3，挤不进必读

### 2.2 修法（不删 arxiv，但让它退居二线）

| 改法 | 工作量 | 效果 |
|---|---|---|
| arxiv priority 2→3，max_items 30→15 | 1 min | 让 hf-papers 几乎必赢；arxiv 当备胎 |
| 加 hf-papers 副线（如 `cool_papers` Kimi 出品） | 30 min | 多一道编辑精选 |
| 加 alphaxiv hot 周榜 | 30 min | 引入人气信号 |

### 2.3 hf-models firehose 同样问题

50 条/天，绝大多数是无名个人上传（含 NSFW），但 quota=1 让它必占一槽。
**修法**：hf-models 加 `min_likes` 阈值（如 ≥ 10）+ 名字关键词过滤；或干脆改用 `https://huggingface.co/api/models?sort=likes30d` 取月榜。

---

## 3. 扩源 backlog（P2）

### 3.1 GitHub releases（建议加 `github_releases` adapter）

仓库 RSS：`https://github.com/<owner>/<repo>/releases.atom`

| name | repo | type | 说明 |
|---|---|---|---|
| vllm-releases | vllm-project/vllm | tool | 推理引擎，月发版 |
| sglang-releases | sgl-project/sglang | tool | 同上 |
| transformers-releases | huggingface/transformers | tool | HF 核心库 |
| llama-cpp-releases | ggerganov/llama.cpp | tool | 本地推理 |
| pytorch-releases | pytorch/pytorch | tool | 大版本里程碑 |
| triton-releases | openai/triton | tool | OpenAI Triton |
| ollama-releases | ollama/ollama | tool | 补 ollama 博客的零 |
| langchain-releases | langchain-ai/langchain | tool | 补博客零 |

### 3.2 编辑精选 paper 流（替/补 arxiv firehose）

| name | URL | 说明 |
|---|---|---|
| cool-papers | https://papers.cool/arxiv/feed | Kimi 出品的"热度+摘要"精选 |
| alphaxiv-hot | https://www.alphaxiv.org/hot/feed | 人气榜 |
| papers-with-code | https://paperswithcode.com/rss.xml | 带代码的论文（如端点活） |

### 3.3 社媒（高价值但工程难）

| 来源 | 工程难点 |
|---|---|
| X/Twitter 重点账号（Sam Altman / Yann LeCun / Andrej Karpathy / Andrew Ng / Andrej Karpathy） | 官方 API 已闭，需 Nitter 代理（不稳）或 RSS 桥（rss.app 等付费） |
| 微博 KOL | 同上，需手撸爬虫 |
| 微信公众号 | 闭环生态，需 wxbot 或 wechatfeed 代理 |
| 小红书 / B 站 | RSSHub 可代理；信噪比中等 |

建议起步：**RSSHub 自托管或公开实例代理 Twitter/微博 重点账号**，5–10 人。

### 3.4 其他编辑精选 newsletter

| name | URL | 说明 |
|---|---|---|
| ben-evans | https://www.ben-evans.com/benedictevans?format=rss | 商业视角 |
| stratechery | (付费墙) | 跳过 |
| the-batch | https://www.deeplearning.ai/the-batch/feed/ | Andrew Ng 周刊 |
| ai-supremacy | https://aisupremacy.substack.com/feed | 综合 |
| jacks-newsletter | https://jack-clark.net/feed/ | OpenAI 联创 Jack Clark 个人 |

---

## 4. 维护 SOP

1. **每周一次**：跑 `--dry-run --publish`，看 `source_fetch_fail` 列表
2. 死链 → 标 `status: manual` 并在 yaml 注释里写"何时死"
3. zero 持续 4 周 → 评估是否真的源出问题（curl 看真返回）；周更/月更源不算异常
4. **新增源**：先 curl 单独验，再加 yaml；priority 默认 3，跑 1 周表现好升 2
5. **firehose 类**（arxiv / hf-models）必须设 `max_items`

## 5. legend

- ✅ working + 上次产出 > 0
- · working + 上次产出 = 0（zero，未必坏，可能周更没新文）
- ❌ working 但 HTTP 失败（死链/限流，待修）
- 🅼 manual（探活后确认无 RSS 或暂时关停）
