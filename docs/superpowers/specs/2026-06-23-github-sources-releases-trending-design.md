# Design — GitHub 源:releases 监听 + trending 发现

- 日期:2026-06-23
- 状态:草案(待 spec review)
- 关联:KANBAN §3 子项目2、[[challenge-premise-before-building]]、[[source-taxonomy-genre-publisher-split]]、ADR 0003(genre/publisher)、#20(HN/Reddit 信号源,同类)

## 目标 / 非目标

**目标**:给打分引入一条新的**采用度信号轴**(工程扩散),靠两个并行机制:
1. **releases 监听** — 盯一份策展的 marquee AI 项目清单(ComfyUI 及生态、OpenClaw、Ollama…),它们的新 release 进日报。
2. **trending 发现** — 广撒网找当下涨星的 AI repo。

**非目标(明确推迟)**:
- issues / PR 监听 — 后续。
- star **速度**(需存历史快照) — v1 只用绝对 star 数。
- **闸 i 跨轮 `seen_repos` 去重** — v1 不做(见 §5),留 v1.1。
- 新增 `release`/`tool` genre — 不做(见 §2,与 ADR 0003 一致)。

## 前提核验(challenge-premise)

- **Trending 无 API,只能抓 HTML** → 和 reddit 同病,很可能在 Actions 出口 IP(Azure 段)403、生产 yield=0。结论:**不把成败押在抓取上**,用 Search API 保底。
- **没有跨轮"已收过"库**:`dedup.py` 只做单轮内 link 去重;Qdrant 沉淀未实现;state.db 无 items 表。所以"和库里比"这道闸**无现成基建** → v1 推迟,只做 repo 自身 recency 闸。

## 1. 两个机制 / 数据源

### releases 监听(adapter: `github_releases`)
- 每个 marquee repo = sources.yaml 一行,`url` 指向 `https://api.github.com/repos/{owner}/{repo}/releases`。
- 拉该 repo releases,保留 `published_at` 落在采集窗口(`CollectionConfig.window_hours`,默认 72h)内的;每个 → 一个 `RawItem`:
  - `title_en` = `"{repo} {tag_name}"`(如 `ComfyUI v0.3.40`)
  - `link` = release 的 `html_url`
  - `raw_summary` = release `body`(changelog,截断到合理长度交给下游)
  - `published_at` = release `published_at`(tz-aware)
  - `genre` = source 行声明(默认 `announcement`)、`publisher` = source 行声明(各 repo 各填)
  - `signals = {"github_stars": stargazers_count}`(额外一次 `GET /repos/{o}/{r}` 取 star)

### trending 发现(adapter: `github_trending`)
- **保底:Search API**。source 行 `url` = 完整查询(沿用 hf-models"url 即完整 API 查询"的现有模式),例:
  `https://api.github.com/search/repositories?q=topic:artificial-intelligence+topic:llm+sort:stars&sort=stars&order=desc&per_page=30`
  - 拉结果,**客户端按 `pushed_at` 过窗口**(闸 ii);每个 → `RawItem`:
    - `title_en` = repo `full_name`(+ 可选 description 进 `raw_summary`)
    - `link` = repo `html_url`
    - `published_at` = `pushed_at`(用最近活动当时间锚)
    - `genre` = source 行(默认 `announcement`)、`publisher` = source 行默认(倾向 `company`)
    - `signals = {"github_stars": stargazers_count}`
- **尽力而为:github.com/trending HTML**(同一 adapter,best-effort 增量):解析 repo 全名列表 → 对每个调 repo API 补 `pushed_at`(闸 ii) + star。**抓取 403/异常 → 返回空,不影响 Search API 主干**(`SourceReport.status` 标 failed,整轮不挂)。

### 鉴权
- 两者优先用 `GITHUB_TOKEN`(Actions 自动注入,经 env 读;搜索/详情走 5000/hr);缺 token 退化到匿名 60/hr,仍可跑。

## 2. Schema / config(零地基改动)

- **不新增 genre**:release 与 trending repo 都归 **`announcement`**(项目官方"有什么新东西",形状同 openai/nvidia 博客)。理由与 ADR 0003 一致——`tool` 是 publisher 不是 genre;`release` 的价值向量若日后被证不准,再带独立 ADR 拆分(升级路径)。
- `publisher` 逐 repo 在 source 行填(releases);trending 发现的匿名 repo 用 source 行的默认 `publisher`。
- `SourceSpec.adapter` 的 `Literal` 增加 `"github_releases"`、`"github_trending"`;adapter 注册表登记。
- **star 信号**:`config/scoring.yaml` 的 `popularity_weights` 加键 `github_stars`(初值 `0.3`,与 `likes` 同档;受 `popularity_cap: 15` 封顶)。打分/enrich 既有逻辑零改动(它们读 `signals` 的已知键)。
- `enrich`:`github_stars` 视为已有 popularity 信号 → 跳过 HN 反查(在 `enrich._has_popularity` 的键集合里加 `github_stars`)。

## 3. 防老两道闸

- **闸 ii(repo 自身更新最近)= v1 做**:adapter 内按 `pushed_at`(trending)/ release `published_at`(releases)过采集窗口。纯过滤,免基建。
- **闸 i(和库里比,跨轮)= v1 不做**:无现成"已收过"库;releases 天然不重复(每版新 link),trending 连发同 repo 暂靠"绝对 star 高 + 最近 pushed"扛。v1.1 加最小 `seen_repos` 表(repo full_name + 首见日 + 冷却期)再解决。

## 4. 已知小缺口(v1 接受)

- 同一项目可能**既作为 trending repo、又作为它的 release** 双重出现(两条 link 不同,单轮 link 去重抓不到)。低频,v1 接受;`seen_repos`(v1.1)或按 repo full_name 跨机制去重时一并解决。
- Search API 按**绝对 star** 排,非当日涨速 → 小而爆的新 repo 不如真榜灵敏;这正是保留 Trending HTML 尽力而为的原因。
- **(2026-06-24 修)** 仅靠 `sort:stars` 会返回老牌巨无霸(AutoGPT/hf-datasets 等,创建上千天、只因日常 push 而"最近活跃")。adapter 现在向 Search `q=` 注入滚动的 `created:>=<now-180d>`(`_inject_created_window`,常量 `_NEW_REPO_DAYS=180`,operator 在 url 写死 `created:` 则不覆盖),只捞**新建**的 repo = 真·新+涨。httpx 会把 `>` 编码为 `%3E`,GitHub 照常接受。

## 5. 测试(TDD,对外只读,天然 `--dry-run`)

- `github_releases`:contract 测试喂 mock releases JSON + repo JSON → 断言条目字段、窗口过滤、`github_stars` 信号、空 releases 优雅返回。
- `github_trending`:
  - Search API:mock search JSON → 断言 `pushed_at` 窗口过滤(闸 ii)、字段、信号。
  - Trending HTML:一个 fixture 解析测试(repo 全名抽取);抓取异常 → 返回空、status=failed。
- golden:`github_stars` → `popularity_weights` 加分映射(与现有 score golden 同风格)。
- 不破坏现有 350 测试。

## 6. 落地顺序(writing-plans 细化)

1. types:`SourceSpec.adapter` 加两值;`popularity_weights`/`enrich` 加 `github_stars`。
2. `github_releases` adapter + contract 测试。
3. `github_trending` adapter(Search 保底 + Trending 尽力)+ 测试。
4. sources.yaml 加 marquee releases 行(ComfyUI 及生态 / OpenClaw / Ollama …)+ 一行 trending(Search 查询 url)。
5. 真实 `--dry-run --score` 验证:确认 Actions 环境下 Search API 出产出、Trending 抓取是否 403(记录到 KANBAN)。
