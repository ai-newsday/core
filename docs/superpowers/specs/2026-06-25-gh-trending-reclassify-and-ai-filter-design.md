# gh-trending 重分类 + 非 AI 过滤 — 设计

日期: 2026-06-25 · 触发: TG 日报里非 AI/过时 repo(Flutter, apple/container)以 92-97 分霸榜

## 问题(实测诊断坐实)

`--dry-run --score` 实跑(input 276 → card pool 25)显示 **25 张卡 11 张是 gh-trending repo(44%)**,
全部 86-97 分,breakdown **逐字相同**:

```
机构影响力 14 + 一手性 20 + 技术价值 10 + 产业影响 16 + 扩散潜力 12
            + 可见指标 15(顶格) + 时效 10 + 惩罚 -5  ≈ 92
```

两个独立病:

- **A 过分**: gh-trending 在 registry 死标 `genre: announcement`(吃最肥内容矩阵 58) +
  `publisher: company`(机构影响力 14),于是**每条 trending repo 自动 ~92**,与内容无关。
- **C 非 AI 漏入**: adapter 的 **HTML 抓取路径**(`github.com/trending?since=daily`,
  [github_trending.py](../../../src/adapters/sources/github_trending.py))**完全没有 topic 过滤**,
  抓通用 trending(所有语言/主题),非 AI repo(apple/container=容器运行时, Flutter=UI 框架)直接进来。
  Search API 路径有 `topic:llm+topic:artificial-intelligence` 双过滤,不是漏点。

(B 灌爆 = 同源占 44%,本轮不治,留后续。)

## 目标 / 验收标准

1. trending repo 不再"每条自动 92":按真实 owner 类型定 publisher、按工具属性定 genre。
2. 非 AI repo(无 AI topic 且 description 无 AI 词)在**抓取阶段**就丢,不进打分。
3. config 驱动(genre/publisher/关键词都在 registry),不写死代码。
4. 外科手术式:只动 `github_trending.py` + registry 一行;不改打分公式、不改 Search API 路径、不动其他源。

## 设计

### 改动 1 — registry ([config/sources.yaml](../../../config/sources.yaml) gh-trending-ai 行)

- `genre: announcement → writeup`(矩阵 12/12/8/9=41,语义=工具/项目)
- `publisher: company → individual`(fallback;adapter 按 owner 覆盖)
- 新增 `keywords:` AI 白名单:
  `[llm, llms, ai, artificial-intelligence, machine-learning, deep-learning, agent, agents,
    agentic, rag, generative-ai, transformer, diffusion, multimodal, nlp, computer-vision]`

### 改动 2 — adapter publisher per-repo (A)

`_item_from_repo` 从 repo JSON 的 `owner.type` 推 publisher:
- `Organization → company`(机构影响力 14)
- `User → individual`(机构影响力 8)
- 缺 `owner`/`owner.type` → 回退 `source.publisher`

genre 仍取 `source.genre`(现 writeup)。`owner.type` 在 Search API items 和 repo API
返回的 repo 对象里都有,两条采集路径都能拿。

### 改动 3 — adapter 抓取路径 AI 过滤 (C)

只对 **HTML 抓取得到**的 repo(经 `https://api.github.com/repos/{full}` 取详情)加过滤,
保留当且仅当:

- repo 的 `topics`(小写集合) ∩ `source.keywords` ≠ ∅,**或**
- description 以**词边界**命中任一关键词(大小写不敏感,如 `\bllm\b` / `\bai\b`,避免 "chair" 误中 "ai")

否则丢弃。Search API 路径**不过滤**(GitHub 服务端已双 topic 过滤)。
`source.keywords` 为 None/空 → 不过滤(向后兼容,其他 trending 源不受影响)。

### 效果(实测 breakdown 反推)

- `apple/container`(topics=[containers,swift,macos], desc 无 AI 词)→ **抓取阶段丢**(C)
- 留下的 trending repo:个人 writeup+individual ≈ **69**,机构 writeup+company ≈ **75**(原 92)(A)

### firehose 闸不误伤

firehose 罚(-20)只打 `genre∈{model,writeup}` + `publisher=individual` + `popularity_proxy==0`。
trending repo 都有 `github_stars>0` → proxy>0 → 不触发。

## 不做(YAGNI / 留后续)

- B 灌爆(同源占比上限 / 加重同源惩罚)——独立后续。
- 不引入 `lab` 特判(知名实验室 org 也归 company,不做 curated 名单)。
- 不新建 `repo`/`tool` genre(复用 writeup)。
- 不过滤 Search API 路径;不改打分公式。

## 测试要点(TDD)

- **A**: `_item_from_repo` 给 owner.type=Organization → publisher=company;User → individual;
  缺 owner → 回退 source.publisher。
- **C**: scrape 到 topics=[swift,macos] 的 repo 被丢;topics=[llm] 的保留;
  description="An LLM agent toolkit" 无 AI topic 但词边界命中 → 保留;
  `source.keywords=None` → 不过滤(全保留)。
- **registry golden**: gh-trending-ai 源 genre==writeup。
