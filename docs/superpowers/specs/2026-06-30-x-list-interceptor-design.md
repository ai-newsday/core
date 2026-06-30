# X (Twitter) List 拦截器 — 设计

日期: 2026-06-30 · 触发: KANBAN §3 P1 "评估 Folo cookie 读 X" / 我们零 X 覆盖 = 最大缺口 (Krea-2 等 X-first 发布完全漏)

## 问题 (根因)

`config/sources.yaml` 0 个 X 源。原因不是没想到, 是 X 没有 RSS / 公开 API 免费档 (memory: [[ai-source-reachability]])。三条路:

- 付费 X API → 拒
- 自托管 RSSHub-X → 拒 (脆 + 封号)
- GH Actions 跑 playwright + cookie → 拒 (GH Actions IP 段被 X 重点监控)

可行: 用户本地浏览器自然浏览 X 时, **浏览器扩展拦截 GraphQL 响应**, 把推文写到 repo, cron 拉取。先例: huntly ([tweet_interceptor.ts](https://github.com/lcomplete/huntly/blob/main/app/extension/src/tweet_interceptor.ts), AGPL-3.0) 已运行多年验证可行。

## 目标 / 验收

1. **零运行时基础设施**: 无 daemon, 无 launchd, 无 pmset wake, 无 VPS, 无 data 信号 repo (extension 代码在 `ai-newsday/x-extension` 独立 repo, 但**数据** ndjson 由 extension 直接 GitHub API PUT 到 core repo `data/x/`)。
2. **被动**: 用户自然打开 X list 才抓; 不打开就不抓; 三天不看 X = 那三天 X 信号空, 不挂 pipeline。
3. **publisher 分层**: 2-4 个 X list 按身份 (lab / kol-en / kol-zh / news) 各自一行 yaml, 接现有 publisher 权重体系。
4. **失败隔离**: extension 抓零 / 推送失败 / X 改 GraphQL schema → 当日 X 信号空, 不挂主 pipeline。

## 设计

### 拓扑

```
┌─ 你的 Chrome (Mac, 任意时间自然打开 X List) ────────────┐
│  [extension MV3]                                         │
│   - content script monkey-patch fetch / XHR              │
│   - URL 正则匹配 ListLatestTweetsTimeline GraphQL        │
│   - 解析 response → 抽 tweet, 按 list_id 分组            │
│   - IndexedDB 去重 (tweet_id 主键, 滚动窗口 7 天)        │
│   - service worker 每 30 min 批量同步:                   │
│     PUT api.github.com/repos/{owner}/{repo}/contents/    │
│         data/x/YYYY-MM-DD.ndjson                         │
│     (PAT 存 chrome.storage.local, options page 配置)     │
└────────────────────┬─────────────────────────────────────┘
                     │ GitHub API PUT (HTTPS)
                     ▼
   ai-newsday/core: data/x/YYYY-MM-DD.ndjson (commit 进 master)
                     │ actions/checkout
                     ▼
┌─ finalize.yml cron (09:00 北京, 1x/day) ─────────────────┐
│  collect step: [x_list adapter] 读 data/x/{今,昨}.ndjson │
│  → yield ContentItem → 进 dedup/score/interpret/publish  │
└──────────────────────────────────────────────────────────┘
```

### 数据流 + 过滤

- **抓**: `ListLatestTweetsTimeline` GraphQL response。其他 GraphQL endpoint (Home / UserTweets / Likes / Bookmarks) **不抓** (避免噪声 + 减少 X 检测面)。
- **保留**: 原创推 + 引用推 (quote-tweet, 带评论的转发)。
- **丢弃**: 纯转推 (RT, 无评论) + 纯回复 (reply)。
- **形态**: 一条 tweet = 一个 first-class `ContentItem`。dedup 层负责跨 X / RSS URL 撞型 (e.g. OpenAI staff 引用官方 release link + openai-rss 也抓到 → 现有 dedup 拍平)。
- **信号轴**: ndjson 保留 `favorite_count / retweet_count / quote_count / reply_count`, MVP 不进 score (跟 Reddit .rss 当前一样靠 recency + publisher), 留作 P1 enrich。

### ndjson schema (一行一条 tweet)

```json
{
  "tweet_id": "1234567890123456789",
  "list_id": "1700000000000000001",
  "author_handle": "sama",
  "author_name": "Sam Altman",
  "text": "(完整推文文本, 含 newline)",
  "quoted_text": "(若为 quote-tweet, 引用源推文文本; 否则缺省)",
  "quoted_author_handle": "(同上, 否则缺省)",
  "permalink": "https://x.com/sama/status/1234567890123456789",
  "created_at": "2026-06-30T14:23:01Z",
  "favorite_count": 1234,
  "retweet_count": 56,
  "quote_count": 7,
  "reply_count": 89,
  "captured_at": "2026-06-30T14:25:11Z"
}
```

### yaml schema

新文件 `config/sources.d/x.yaml` (registry loader 已支持 sources.d/ overlay, 见 [[sources.d-overlay]]):

```yaml
- {name: x-ai-lab,    url: "xlist:TBD", publisher: lab,        genre: announcement, adapter: x_list, status: manual, priority: 1}
- {name: x-ai-kol-en, url: "xlist:TBD", publisher: individual, genre: writeup,      adapter: x_list, status: manual, priority: 2}
- {name: x-ai-kol-zh, url: "xlist:TBD", publisher: individual, genre: writeup,      adapter: x_list, status: manual, priority: 2}
- {name: x-ai-news,   url: "xlist:TBD", publisher: media,      genre: news,         adapter: x_list, status: manual, priority: 2}
```

`url` 字段用 sentinel `xlist:<list_id>` 编码 list_id (SourceSpec.url 必填, 复用此字段而非加新 schema)。`list_id` 占位 "TBD", PR-3 落地后建 X list 取 ID 填回 + `status: working`。

### `x_list` adapter 契约

输入: `./data/x/*.ndjson`。文件名按 **UTC date** (extension `captured_at.slice(0,10)`); adapter 默认读 today-UTC + yesterday-UTC 两文件 (finalize 跑在 01:00 UTC = 09:00 北京, today-UTC 只有 1 小时数据, yesterday-UTC 覆盖了用户睡前的浏览 + 美东工作时间, 两天合并对北京晨报最关键的 24h 信号窗口刚好覆盖)。跨天去重 (tweet_id 已带 `x:` 前缀) 交给 dedup 层。

转换:
```
tweet_id     → external_id = "x:" + tweet_id
text         → title = _tweet_title(text, 140); body = "@" + author_handle + ":\n" + text + ("\n\n> 引用 @" + quoted_author_handle + ": " + quoted_text if quoted_text)
created_at    → published_at
permalink     → url
list_id       → 反查 yaml: source.url == "xlist:{list_id}" → source.name (没匹配 → 丢, log warning)
favorite/rt/quote/reply → signals = {"x_favorite", "x_retweet", "x_quote", "x_reply"} (flat key 风格对齐 hn_points; 暂不进 score 由 popularity_weights 为空兜住)
```

`_tweet_title(text, n)` 规则 (对齐 `interpret._trim_to_sentence` 模式, 增加 "推文第一行常是结论" 的偏好):

1. 取 `text.split("\n", 1)[0].strip()` 作为第一行候选。
2. 长度 ≤ `n` → 直接用。
3. 超长 → 在前 n 字符窗口里:
   - 优先按句末标点切 (`。！？!?.`, 复用 `_SENT_ENDS` 常量)
   - 无句末 → 按最后空格切 (英文场景), 切点 > n/2 才接受
   - 都没有 → 硬切 + `…`

n 默认 140 (≈推文上限的一半, 给 LLM 后续翻译/精炼留足语义), **大于** interpret 层 `title_max_chars=64` —— 这是因为本字段是 `title_en` (原文), 给 LLM 输入用; 最终发布的中文 `title` 仍受 interpret/review 层 64 字夹紧, 自然二次收敛。

source name (e.g. `x-ai-lab`) 用于 publisher 分层打分。

### ContentItem 映射示例

输入 ndjson 行:
```json
{"tweet_id":"123","list_id":"L1","author_handle":"sama","text":"GPT-5 is...","permalink":"https://x.com/sama/status/123","created_at":"2026-06-30T14:23:01Z","favorite_count":1000}
```

yaml: `x-ai-lab` 的 `list_id: L1`。

输出 RawItem:
- `source = "x-ai-lab"`
- `title_en = "GPT-5 is..."` (`_tweet_title` 取第一行, ≤140 char, 句末/词界切)
- `link = "https://x.com/sama/status/123"`
- `raw_summary = "@sama:\nGPT-5 is..."`
- `genre = announcement`, `publisher = lab` (从 SourceSpec 复制)
- `published_at = 2026-06-30T14:23:01+00:00`
- `signals = {"x_favorite": 1000, "x_retweet": 50, "x_quote": 3, "x_reply": 7}`
- `fetched_via = "native"`

(注: x_list 不输出 `external_id` —— RawItem 没这字段; 去重靠 `link` 唯一性)

边界测试 (PR-1 fixture 至少覆盖):
- `"GPT-5 is here."` → title = `"GPT-5 is here."`
- `"GPT-5 is here.\nDetails below."` → title = `"GPT-5 is here."`  (取第一行)
- 200 字符无句末英文长句 → 在 ≤140 词界处切 + `…`
- 200 字符纯中文无句号 (推特罕见) → 硬切 + `…`
- 含 emoji + URL 的推文 → emoji 字节长度按 Python len 计, URL 不裁开

### Extension 模块

3 文件 (~250 行 TS), 装在新 repo `ai-newsday/x-extension`:

| 文件 | 职责 | 参考 |
|---|---|---|
| `request_interceptor.ts` | monkey-patch `window.fetch` + `XMLHttpRequest.prototype.open/send`, 命中 URL 正则时把 response text 喂给 handler | huntly 同名文件 (技术参考, 不复制) |
| `tweet_extractor.ts` | 解析 X GraphQL response (`data.list.tweets_timeline.timeline.instructions[].entries[]`) → ndjson 行; 丢 RT/reply | 新写 |
| `github_sync.ts` | IndexedDB → 每 30 min 调 `PUT /repos/{owner}/{repo}/contents/data/x/YYYY-MM-DD.ndjson` (file existed → GET sha → PUT new content) | 新写 |
| `options.html` + `options.ts` | 配置 PAT + repo + GitHub commit author email | 新写 |
| `manifest.json` | MV3, host_permissions: `https://x.com/*`, `https://api.github.com/*` | 新写 |

**许可证**: 新 repo MIT (huntly 是 AGPL-3.0; 我们参考思路 + 自己实现 fetch monkey-patch, 不复制代码, 不被 AGPL 传染)。

### 失败降级

| 故障 | 行为 |
|---|---|
| 用户三天不开 X | data/x/YYYY-MM-DD.ndjson 缺失 / 行数少 → adapter yield 少或 0 → 不挂 pipeline |
| extension 抓零 (X 改 GraphQL schema) | tweet_extractor 解析失败抛 → service worker catch → 写 console + chrome.notifications, 不污染 ndjson |
| GitHub API PUT 失败 (PAT 过期 / rate-limit) | service worker retry 3 次 + exponential backoff; 全失败 → IndexedDB 暂存, 下次 30 min 周期再试 |
| ndjson 文件大到 GitHub API 拒收 (>1 MB) | 切按小时分片 `data/x/YYYY-MM-DD-HH.ndjson` (P1, MVP 单日 ~50 KB 远未触及) |
| `list_id` 在 yaml 没匹配 | adapter log warning + 跳过, 不挂 |
| ndjson 损坏 (一行不是合法 JSON) | adapter 按行 try/except, 坏行计数 log, 不挂 |

## 替代方案 (考虑过 + 拒)

| 方案 | 拒因 |
|---|---|
| 付费 X API (X API v2 Basic $100/mo) | 成本 |
| 自托管 RSSHub-X | 脆 + 封号风险 |
| GH Actions playwright + cookie | GH Actions 出口 IP 段被 X 重点监控 |
| Folo cookie 读 X (KANBAN §3 P1 原方案) | Folo RSS reader 免费档不包含 X (Folo X 接入是 AI 付费功能); 探活验证 |
| 本地 daemon + launchd + pmset wake mac 3x/day | 工程过重, huntly 证明被动模式够用 (前一次设计稿被用户拒) |
| 单独 `ai-newsday/x-signals` repo (extension push, cron pull) | 两个 repo 维护, 不如 extension 直接 PUT 到 core; (前一次设计稿被用户拒) |
| 抓 Home / Following timeline | publisher 不分层, 算法推荐流混入噪声; 与 KOL 直 follow tweet 同等权重失真 |

## 实施顺序 (3 PR)

| # | repo / 分支 | 内容 | 验收 |
|---|---|---|---|
| **PR-1** | `ai-newsday/core` | `src/adapters/x_list.py` + `tests/fixtures/x_list_sample.ndjson` (手造 ≥5 条覆盖 quote / 长 text / list_id 不匹配) + pytest + `config/sources.d/x.yaml` (`list_id: "TBD"`, `status: manual`); 不上 cron | pytest 全绿; `--dry-run` 不报错 |
| **PR-2** | `ai-newsday/core` | finalize.yml collect step **无需改动** (data/x/ 已在 checkout 范围); 手 commit 一份 `data/x/2026-07-01.ndjson` 验证 cron 能读到 + adapter yield + 进 candidate pool (status 改 `working`, list_id 仍 "TBD" 等 extension 落) | dry-run funnel 报告里 x-* 4 个 source 有 yield |
| **PR-3** | 新 repo `ai-newsday/x-extension` | MV3 extension 三文件 + options page + 装机 README; 用户本地装 + 建 X list + 取 list_id 填回 core 的 x.yaml | 本地 Chrome 装 unpacked extension, 打开 X list 看到 ndjson 30 min 后 PR 到 core master |

PR-1 + PR-2 ≤ 200 行, 一周内合。PR-3 是真正风险点 (extension + X GraphQL schema), 但 PR-1/2 已经把 pipeline 路径验通, PR-3 任意失败不挂主线。

## 测试矩阵

| 层 | 测试 | 类型 |
|---|---|---|
| `x_list` adapter | fixture ndjson → assert ContentItem 字段映射 | 单元 (pytest) |
| `x_list` adapter | list_id 不匹配 → 丢弃 + log warning | 单元 |
| `x_list` adapter | 损坏 ndjson 行 → skip + 不抛 | 单元 |
| `x_list` adapter | 跨日合并 (今日 + 昨日两文件) | 单元 |
| registry loader | x.yaml 加载 → 4 个 source 注册 | 已有 contract test 覆盖 |
| Extension | 手测 (X GraphQL response shape 易变, 无单测价值) | 装机 smoke |

## 不做 (YAGNI)

- auto-scroll (huntly 不做, 我们也不做; 用户自然浏览到底就够)
- Following / Home / UserTweets / Likes / Bookmarks 拦截 (扩面引入噪声 + X 检测面)
- 信号轴入 score (留 P1, 同 Reddit .rss 当前)
- 每小时分片 ndjson (单日 50 KB 远未到 GitHub API 限)
- 多用户 / 多 Chrome profile (个人项目, 一人一装)
- extension 自动更新机制 (用户每次 git pull x-extension 自己 reload)
- 跨设备 (你只在 mac 装就行; 多设备时 IndexedDB 不同步是 feature 不是 bug)

## 后续可加 (写明上限 + 升级路径)

- ponytail: extension 仅在用户打开 list tab 触发, 升级路径 = options 加 "also_capture_following" 开关 + URL 正则加一条 (ListLatestTweetsTimeline | HomeTimeline | HomeLatestTimeline)
- ponytail: ndjson 单日单文件 ≤1 MB, 升级路径 = 切小时分片
- ponytail: PAT 存 chrome.storage.local 明文, 升级路径 = OAuth GitHub App
- ponytail: 全日 1x cron, 升级路径 = KANBAN §3 P2 多频率 (4H tick) 独立 spec

## 关联

- 参考 (技术): [huntly tweet_interceptor.ts](https://github.com/lcomplete/huntly/blob/main/app/extension/src/tweet_interceptor.ts) (AGPL-3.0, 不复制代码)
- KANBAN: §3 P1 "评估 Folo cookie 读 X" 本 spec 替代该方案
- ADR: 暂不开新 ADR (沿用 ADR 0003 genre/publisher 分层, 本 spec 只是新 adapter)
- 后续 spec: KANBAN §3 P2 多频率出报 (与本 spec 解耦, 独立项目)
