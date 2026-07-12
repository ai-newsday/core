# 产品质量 metrics dashboard — 设计

日期: 2026-07-01 · 触发: KANBAN §3 P0 元任务 (#59) — 用户反馈"产出 post 不行", 但**目前无数据支撑**, 只能靠肉眼看 keep/drop。必须先落 metrics 才能量化其他 P0 (翻译治根 / Paper+Releases 降噪) 的效果。

## 问题

现状: 每天 pipeline 跑一次落 `data/runs/<uuid>/` 7 个 jsonl (source_reports/collected/deduped/scored/interpreted/reviewed/feedback_events), 但**无按日聚合**, **无 fallback_rate 遥测**, **无每日质量指标推送**。

用户投诉 "post 不行" 时:
- 我不知道今天 fallback 触发了几次 (extractive_fallback 卡上贴 ⚠️ 徽章但不 log 统计)
- 我不知道今天哪个源产出噪声最多
- 我不知道 quota 层砍掉了多少
- 修完某个 P0 (如翻译治根) 后, 我不知道 fallback_rate 有没有真降

## 目标 / 验收

1. **每日一张图 + 一份 JSON 到手**: 每次 finalize 出报后, TG 收到一条 photo + 简报 caption; 同步 JSON + PNG 到 git; Hugo 站点自增 `/metrics/YYYY-MM-DD` 页。
2. **funnel 逐层可见**: 一图能看到 candidates → dedup → quota → interpret → review → posted 每层剩多少 / 掉多少。
3. **翻译 KPI 直接**: fallback_rate 是核心数字, 图上 7d 趋势线, JSON 里精确值, 掉了的 title 样本存 JSON。
4. **噪声 KPI 直接**: per-genre 与 top-N per-source 的 candidates→posted 比。
5. **零基础设施**: 复用 finalize.yml + Pages workflow + Telegram bot, 无新 job 无新 cron。
6. **不阻断日报**: metrics 失败 → finalize 主流程已完成, 不重跑不 rollback。

## 设计

### 数据流

```
finalize.yml (北京 09:00 = UTC 01:00)
   ├─ actions/checkout (PAT)
   ├─ uv sync
   ├─ Restore state.db cache
   ├─ Run finalize tick   (--tick finalize; 写 content/posts/YYYY-MM-DD.md)
   ├─ Run metrics tick    ← 新增
   │    ├─ 找最新 data/runs/<uuid>/ (mtime)
   │    ├─ 读 7 个 jsonl → 计算 funnel + rates + per_genre + per_source
   │    ├─ 读过去 6 天的 content/metrics/*.json → 拼 7d 趋势
   │    ├─ 写 content/metrics/YYYY-MM-DD.json
   │    ├─ 生成 content/metrics/YYYY-MM-DD.png (matplotlib, 2 subplot)
   │    ├─ 写 content/metrics/YYYY-MM-DD.md   (Hugo front-matter + embed png + 表)
   │    └─ 发 TG photo (path=png) + caption
   └─ Commit + push (content/metrics/* 随 content/posts/* 一起入库, 触发 pages.yml)
```

### 落盘布局

```
content/
  posts/
    2026-07-01.md            # 日报 (已有)
  metrics/                    # 新
    2026-07-01.json           # 事实数据, 未来 dashboard 也吃这个
    2026-07-01.png            # 图, TG + Hugo 复用同一路径
    2026-07-01.md             # Hugo 页面 (front-matter + png embed + 数字表)
    index.md                  # 列表页 (可选, P1 加)
```

Hugo 会把 `content/metrics/YYYY-MM-DD.md` 编译成 `https://ai-newsday.github.io/core/metrics/YYYY-MM-DD/` 页面。

### JSON schema

`date` 使用**北京时间日期** (对齐 `content/posts/YYYY-MM-DD.md` 命名), 因为 finalize 是"北京 09:00 出报, 汇总昨天完整一天"。`generated_at` 用 UTC ISO8601 (机器时间)。

```json
{
  "date": "2026-07-01",
  "run_id": "b41fd957-228a-438c-b8e5-1d97a4726c54",
  "generated_at": "2026-07-01T01:15:23Z",
  "funnel": {
    "candidates": 87,
    "after_dedup": 68,
    "after_score_quota": 24,
    "interpreted_ok": 21,
    "interpreted_fallback": 3,
    "review_eligible": 20,
    "posted": 12
  },
  "rates": {
    "fallback_rate": 0.125,
    "dedup_reduction": 0.218,
    "quota_reduction": 0.647,
    "interpret_fail_rate": 0.125,
    "keep_rate": 0.60
  },
  "per_genre": {
    "paper":        {"candidates": 32, "posted": 1, "noise_ratio": 0.969},
    "release":      {"candidates": 18, "posted": 2, "noise_ratio": 0.889},
    "announcement": {"candidates": 15, "posted": 4, "noise_ratio": 0.733},
    "news":         {"candidates": 12, "posted": 3, "noise_ratio": 0.750},
    "writeup":      {"candidates": 8,  "posted": 2, "noise_ratio": 0.750},
    "model":        {"candidates": 2,  "posted": 0, "noise_ratio": 1.000}
  },
  "per_source_top10": [
    {"name": "hf-papers",       "yield": 30, "kept": 1, "noise_ratio": 0.967},
    {"name": "github-releases", "yield": 15, "kept": 2, "noise_ratio": 0.867},
    {"name": "the-decoder",     "yield": 8,  "kept": 3, "noise_ratio": 0.625}
  ],
  "samples": {
    "fallback_titles": [
      "OpenAI releases GPT-5 turbo variant with...",
      "Anthropic MCP server SDK 0.5.0 release notes",
      "Google DeepMind Gemini 2.5 flash preview"
    ]
  },
  "trend_7d": {
    "dates":           ["2026-06-25","2026-06-26","2026-06-27","2026-06-28","2026-06-29","2026-06-30","2026-07-01"],
    "fallback_rate":   [0.15,        0.18,        0.11,        0.14,        0.20,        0.16,        0.125],
    "eligible_rate":   [0.12,        0.10,        0.15,        0.13,        0.08,        0.14,        0.138]
  }
}
```

### PNG 图 (matplotlib, 2 subplot 竖排)

- 尺寸: **800 × 700 px**, PNG 优化 ≤ 60 KB
- 白底, matplotlib 默认 seaborn 风格
- 无 emoji, 纯中英混排

```
┌────────────────────────────────────────────┐
│  ax1: 7d 趋势线                             │
│    x = 日期 (7 点)                          │
│    y = 比例 [0.0, 1.0]                      │
│    line 1: fallback_rate  (红)              │
│    line 2: eligible_rate  (绿)              │
│    图例右上; 标题 "7d 趋势 (翻译 KPI + 合格率)" │
├────────────────────────────────────────────┤
│  ax2: 今日 funnel waterfall (水平条)        │
│    y = 层名 (6 层, 从上往下)                 │
│    x = 剩余 item 数 (bar 长度)              │
│    每 bar 尾标数字 + 相对上一层的 "-N (-X%)" │
│    颜色: candidates 灰, dedup 淡蓝,          │
│         quota 淡橙, interp 淡黄,             │
│         review 淡绿, posted 深绿             │
│    标题 "今日 funnel (2026-07-01)"           │
└────────────────────────────────────────────┘
```

第一层 candidates 为满长, 依次收缩。数字标注让"哪层砍最狠"一眼可见。

### Hugo `.md` 页面 (`content/metrics/YYYY-MM-DD.md`)

```markdown
---
title: "Metrics 2026-07-01"
date: 2026-07-01T09:15:00+08:00
type: metrics
draft: false
---

![funnel](./2026-07-01.png)

## 核心指标

| 指标 | 值 |
|---|---|
| 候选 (candidates) | 87 |
| 合格 (posted) | 12 |
| 合格率 | 13.8% |
| fallback_rate (翻译 KPI) | 12.5% |
| 最大损失层 | quota (掉 64.7%) |

## per-genre 噪声比

| genre | candidates | posted | 噪声比 |
|---|---|---|---|
| paper | 32 | 1 | 96.9% |
| release | 18 | 2 | 88.9% |
| ... | | | |

## fallback 样本 (翻译失效的 title)

- OpenAI releases GPT-5 turbo variant with...
- Anthropic MCP server SDK 0.5.0 release notes
- Google DeepMind Gemini 2.5 flash preview

[原始 JSON](./2026-07-01.json)
```

### TG 消息

**类型**: `send_photo` (需要给现有 telegram bot adapter 加 `send_photo` 方法, 现在只有 `send_message`)

**photo**: `content/metrics/YYYY-MM-DD.png` 文件二进制

**caption** (HTML parse mode, 精简, 单 emoji 引路):

```
📊 metrics 2026-07-01

候选 87 → 合格 12 (13.8%)
fallback 3 (12.5%)  ← 翻译 KPI
最大损失: quota 层 -64.7%
top 噪源 hf-papers: 30→1 (96.7%)

<a href="https://ai-newsday.github.io/core/metrics/2026-07-01/">详情</a>
```

用户 TG 一屏看到图 + 数字 + 链接。

### `--tick metrics` CLI

新增 CLI tick, `src/cli.py` 加个 `run_metrics` 分支:

- 参数: 无
- 行为:
  1. `latest_run = max(data/runs/*, key=mtime)`
  2. 读 7 个 jsonl 计算 funnel + rates + per_genre + per_source
  3. 读 `content/metrics/*.json` 过去 6 天 → 拼 `trend_7d`
  4. 写 3 个文件 (json/png/md)
  5. 调 `TelegramBot.send_photo(...)` (若 `TELEGRAM_BOT_TOKEN` 存在)
  6. `--dry-run` 支持: 只写不发 TG

### 失败降级

| 故障 | 行为 |
|---|---|
| `data/runs/` 为空 (finalize 未跑) | metrics tick log warning, exit 0 |
| jsonl 读损坏 | 尽力算能算的字段, 缺的填 `null`, log warning |
| matplotlib 生成 PNG 失败 (罕见) | 写 JSON + md, TG 只发 caption 不带 photo, log error |
| 过去 6 天 metrics.json 缺 | trend_7d 该点位 null, 前端画线跳点 |
| TG send_photo 失败 | log error, exit 0 (JSON/PNG 已入库, Hugo 页仍生成) |
| Hugo 编译失败 | 与现有日报同样处理路径, 不特殊化 |

## 替代方案 (拒)

| 方案 | 拒因 |
|---|---|
| 独立 `metrics.yml` workflow | 与 finalize 解耦无收益, 多一个 cron 触发时机也更复杂 |
| Grafana / Plotly Dash | 重, 需要服务器持续跑, 一天一次数据不值得 |
| 静态 SVG 图 (无 matplotlib) | 手写 SVG 每次改 layout 都痛; matplotlib 是 stdlib-ish 熟工具 |
| pie chart 代替 waterfall | 用户拍板 waterfall (KANBAN §3 P0 首要目的=指向"该治哪层") |
| 保留每天 PNG 变 transient (生成即发 TG, 不进 git) | 拆两条路径: TG 用一份, Hugo 又用另一份, 复杂度 > 存储成本 |
| 保留策略修剪 | 18 MB/年可接受, 保留全部易查历史 |

## 实施顺序 (建议 3 commit, 单 PR)

| # | 内容 | 验证 |
|---|---|---|
| **C1** | `src/pipeline/metrics.py` 纯函数 (jsonl → JSON 数据) + contract test | pytest 全绿 |
| **C2** | `src/pipeline/metrics_render.py` (JSON → PNG + md) + golden test (PNG 生成不校像素只校尺寸/大小, md 校 snapshot) | pytest + snapshot 全绿 |
| **C3** | `src/cli.py` 加 `run_metrics` + `TelegramBot.send_photo` + `.github/workflows/finalize.yml` 加 `--tick metrics` 步 + Hugo 侧任何配置 | dry-run 本地跑, 生成 3 文件 |

## 测试矩阵

| 层 | 测试 | 类型 |
|---|---|---|
| `metrics.py` `compute_funnel(run_dir)` | fixture run_dir 输入 → 断言 funnel 计数 | contract |
| `metrics.py` `compute_rates(funnel)` | fallback/keep/quota 三种数学 | 纯单元 |
| `metrics.py` `compute_per_genre(items)` | 3-genre fixture → 断言 noise_ratio | 单元 |
| `metrics.py` `load_trend_7d(metrics_dir, today)` | 3 天历史 + 4 天缺 → 填 null | 单元 |
| `metrics_render.py` `render_png(data)` → PNG bytes | 断言 PNG 头 + 大小合理 | 单元 |
| `metrics_render.py` `render_md(data)` | snapshot | golden |
| `send_photo` | mock Bot API, 断言参数 | contract |
| e2e | 手 fixture run_dir → `--tick metrics --dry-run` → 断 3 文件生成 | integration |

## 不做 (YAGNI)

- 实时看板 / 交互 hover (静态 PNG 够, 想动手再上 Plotly)
- 阈值告警 (fallback_rate > X 通知) — 先看数据再定规则, 现在拍脑袋定阈值无意义
- 全量 per-source (只 top 10, 其余 JSON 里存但不上图)
- CDN / 图片压缩 pipeline (matplotlib 输出 ≤60KB 够 TG 单条上限)
- index.md 列表页 (P1, 未来累积再加)
- SVG / 矢量输出 (PNG 够 TG 显示 + Hugo embed)
- 各种 rate 的置信区间 / 显著性检验 (样本一天几百条, 统计不到位)

## 后续可加 (ponytail: 上限 + 升级路径)

- ponytail: **保留策略**永久, 升级路径 = 若 git 仓库超 500 MB, 加 `content/metrics/archive/` 归档旧图
- ponytail: **TG 消息**手写 caption 拼接, 升级路径 = 加模板引擎 (若 caption 逻辑复杂化)
- ponytail: **单 PNG**, 升级路径 = 拆多张 (per-genre 各一张) 塞 TG album
- ponytail: **finalize.yml inline**, 升级路径 = 若 metrics 计算 > 30s 影响 finalize, 拆独立 workflow

## 关联

- KANBAN §3: P0 "产品质量 metrics dashboard (元任务)" — 本 spec
- 上游: `data/runs/<uuid>/*.jsonl` (七层 pipeline 落盘)
- 后续 P0 (依赖本 dashboard 验证): 翻译失效根治 (监 fallback_rate) / Paper+Releases 降噪 (监 per_genre.noise_ratio)
- Backlog §4: "子项目 4: 每轮漏斗报告" 与本板 overlap (per-run 详情 vs per-day 聚合) — 未来若要 per-run 详情, 可复用本 spec 的 metrics.py 计算函数
