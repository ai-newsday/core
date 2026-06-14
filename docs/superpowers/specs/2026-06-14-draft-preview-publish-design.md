# 草稿预览发布工作流 — 设计文档

| 项目 | 内容 |
| --- | --- |
| 日期 | 2026-06-14 |
| 状态 | Approved（待写 plan） |
| 关联 | PRD P1「多端发布」、P2「GitHub Actions cron」；spec `docs/specs/publish.md` |
| 前置 | delivery channels（Telegram + Website + SQLite）已完成；finalize/publish workflow 框架已在 master |

## 1. 目标与边界

打通日常使用闭环的最后一公里：**finalize 出草稿 → 以网页形式预览确认 → 手动 publish 定稿**。

生死线（PRD）：每天人工介入 ≤ 10 分钟，只做"取舍"不做"重写"。本层让作者睡前能在浏览器里扫一眼当天草稿，确认无误后一键发布。

**范围内**：
- 用 **Hugo + PaperMod** 主题把日报渲染成静态站，部署到 GitHub Pages
- finalize 产物加 Hugo front matter（`draft: true`），commit 后预览站自动 build（含 drafts）
- publish 把 `draft: true` → `false`，发 Telegram 终稿

**范围外（下个项目）**：实时 AI 新闻网（不止日报、含实时推送）。本层只做日报归档站，为将来迭代留好内容结构。

## 2. 数据流

```
collect cron (3x/day) → Telegram 审阅
                            ↓
                    finalize (手动/睡前)
                            ↓
              content/posts/YYYY-MM-DD.md (draft: true)
                            ↓
              GH Pages 预览 (hugo -D, build drafts)
                            ↓
                    publish (手动确认)
                            ↓
              draft: false + Telegram 终稿
                            ↓
              GH Pages 重新 build (正式版)
```

## 3. Front Matter 契约

`publish.py` 新增纯函数 `render_front_matter(report, date_label, draft: bool) -> str`：

```yaml
---
title: "AI Daily · 2026-06-14"
date: 2026-06-14T20:00:00+08:00
draft: true
tags: ["官方", "论文", "模型"]
summary: "今日 8 条：OpenAI 上 AWS、统一生成新论文……"
---
```

契约：
- `draft` 是唯一状态开关——finalize 永远 `true`，publish 翻 `false`
- `date` 用东八区（作者审阅时区），决定 Hugo 归档排序
- `tags` = 当日 report 中出现的 `source_type` 中文标签，去重后按 `PublishConfig.type_labels` 的声明顺序排列（喂 PaperMod 标签云）
- `summary` = `daily_take` 截前 140 字（无则空）；140 是 PaperMod 列表页摘要的舒适上限
- front matter 之后拼接现有 `render_markdown` 正文（分类速览 + 数据概览），正文逻辑零改动

**卡片正文补充**：当前 `render_markdown` 的 `_render_categories` 未渲染 takeaway。本层补上：每条新闻显示 `title + summary + takeaway + tags + 原文链接`，takeaway 有则显示、空则跳过。

`publish.py` 另加纯函数 `flip_draft(text: str) -> str`：把 front matter 里 `draft: true` 替换为 `draft: false`，幂等（已是 false 不变）。

## 4. CI/CD 改造

三个 workflow 职责：

1. **`finalize.yml`**（改造）—— 跑 finalize tick 生成 `content/posts/YYYY-MM-DD.md`（`draft: true`），commit。不动 Pages。
2. **`pages.yml`**（新增）—— 监听 `content/` 路径 push，跑 `hugo -D`（build drafts）部署 GH Pages。finalize commit 后几分钟预览站即更新。
3. **`publish.yml`**（改造）—— 手动触发，`flip_draft` 当天页，发 Telegram 终稿，commit。pages.yml 随之重 build。

**Hugo 工程落点**（仓库新增）：
- `hugo.toml` —— 站点配置 + PaperMod + 中文
- `themes/PaperMod/` —— **vendor**（直接拷进仓库，不用 submodule），避免 CI 拉 submodule 复杂度；代价是主题更新需手动
- `content/posts/` —— finalize 输出落点（Hugo 惯例）
- `public/` —— Hugo build 输出，加进 `.gitignore`

`docs/daily/` 废弃。`WebsiteConfig.output_dir` 默认从 `docs/daily` 改 `content/posts`。

## 5. 改动清单

1. `src/pipeline/publish.py` —— 加 `render_front_matter` + `flip_draft` 纯函数；`render_markdown` 拼 front matter；`_render_categories` 补 takeaway
2. `src/core/types.py` / `src/core/config.py` —— `WebsiteConfig.output_dir` 默认 → `content/posts`
3. 仓库新增 —— `hugo.toml`、`themes/PaperMod/`(vendor)、`.github/workflows/pages.yml`
4. `.github/workflows/finalize.yml` / `publish.yml` —— 改输出路径、加 draft 翻转
5. `.gitignore` —— 加 `public/`
6. 测试 —— 见 §6

## 6. 测试策略

TDD，纯函数 + 配置可测；CI workflow 本身不在单测范围。

| 测试 | 类型 | 断言 |
| --- | --- | --- |
| `render_front_matter` draft=true | contract | 含 `draft: true`、`date` 东八区、`tags` 去重排序 |
| `render_front_matter` draft=false | contract | `draft: false`；其余同上 |
| tags 聚合 | contract | 多条目 source_type → 去重中文标签列表 |
| takeaway 渲染 | contract | 卡片含 takeaway（有则显，空则跳） |
| 完整页 = front matter + 正文 | golden | fixture report → 完整 .md snapshot |
| `WebsiteConfig` 默认路径 | contract | `output_dir == "content/posts"` |
| `flip_draft` | contract | draft:true → false；幂等；无 front matter 不崩 |

**不测**：Hugo build（CI 实跑暴露）、GH Pages 部署（同）、`hugo.toml`/主题（配置/vendor）。

## 7. 验收标准

1. finalize workflow 跑完，`content/posts/YYYY-MM-DD.md` 存在且 `draft: true`
2. pages.yml build 成功，预览站能看到当天草稿页（含 score/tags/takeaway）
3. publish workflow 跑完，同页 `draft: false`，Telegram 收到终稿
4. 全部 7 个新测试 + 现有 263 测试全绿
5. 一条新闻卡片完整呈现 title/summary/takeaway/tags/原文链接
