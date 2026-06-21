# M2-A 设计 — 文风内容契约 + 渲染重做

- 日期:2026-06-21
- 来源:M2 brainstorm（编辑方向已锁在 `references/editorial-and-format-sop.md`,本设计=实现层）
- 状态:设计待用户复审 → writing-plans

## 1. 目标

按 `references/editorial-and-format-sop.md`(v0.2) 把日报从"emoji + 一句话/对你/锐评填空格子 + 必读重复 + 数据概览"改成"无 emoji + 钩子标题 + 一段顺读正文 + 必读不重复 + 朴素页脚"。核心=一次**纵向字段合并**:`summary/takeaway/hot_take` → 单一 `body`,再让 prompt 产成段、渲染去 emoji。

**不在本设计(留 M2-B / 甲-3):** 配额提到 10/上限 11、软配额+质量地板、垃圾空条目/firehose 噪声过滤。本轮只改"长什么样",不改"选几条/选什么"。

## 2. 数据模型（`src/core/types.py`）

`InterpretedItem`:
- **删** `summary`、`takeaway`、`hot_take`。
- **加** `body: str`(一段顺读正文)。
- **保留** `title`(钩子标题)、`tags`(恰好 3)、`evidence`、`interpretation_status`、`eligible_for_must_read`、`quality_flags`,以及继承自 `ScoredItem` 的 `title_en/source/link/genre/score/signals` 等。

## 3. Prompt（`src/prompts/interpret_item.md`）

产 `{title, body, tags, evidence}`(去掉 summary/takeaway/hot_take):
- `title`:中文钩子标题,带数字/反差(例「0.22B 反超 11.9B」),≤64 字;模型名/公司名/技术名**保留英文原文**(Image Inpainting、inference、agent…)。
- `body`:**一段**顺读正文,≤180 字,结构=事实 →"你能拿它做什么"(实用,落到可操作)→ 可选一句克制判断/信号;**无 emoji、无"一句话/对你/锐评"标签、不堆形容词、不油**。
- `tags`:恰好 3 个,# 开头。
- `evidence`:关键事实→原文锚点,anchor 只能取自 link/related_links,无则空数组。
- 输出 JSON:`{"title":"...","body":"...","tags":["#x","#y","#z"],"evidence":[{"claim":"...","anchor":"..."}]}`

`src/prompts/daily_take.md` 不变(今日看点本就是成段,SOP 保留)。

## 4. 传播层改动

### 4.1 `src/pipeline/interpret.py`
- 解析新 schema:`body = str(parsed.get("body",""))`;不再读 summary/takeaway/hot_take。
- 必读资格:`eligible = bool(body) and len(evidence) >= config.min_evidence`(原来用 takeaway)。
- 抽取式回退:`body` 用 `raw_summary`(或截断的原文)填充,`title` 用现有回退逻辑;回退条目 `eligible=False` 延续(无 evidence)。
- daily_take 输入项(原 `f"- {title}: {it.summary}"`)改用 `it.title`(更短,够主编串趋势)。

### 4.2 `src/pipeline/review.py`
- `EDITABLE_FIELDS = ("title", "body", "tags", "evidence")`。
- `_gate(status, evidence, body, config)`:`status=="ok" and len(evidence)>=min and body!=""`。
- 调用处把传 `takeaway` 改传 `body`。

### 4.3 `src/pipeline/selfcheck.py`
- `format_lint`:必读条目缺 `body` → flag(原查 takeaway)。
- `_FIELD_WHITELIST = {"body","title","tags","evidence"}`。
- critic prompt 占位注入:`{{body}}: item.body`(去掉 summary/takeaway/hot_take 注入)。同步改 `src/prompts/selfcheck.md` 里引用的字段(若有)。

### 4.4 `src/pipeline/tick.py`
- `_build_card(item)` → `{title, body, tags, source, link, score, signals, source_label}`(去 summary_zh/takeaway/hot_take)。
- `pending_reviews` 表 schema **不动**(避免改 committed 的 state.db,#25 的对象):`db.upsert_pending_review` 调用处把 `body` 存进现有 `summary_zh` 列、`takeaway`/`hot_take` 列填 `""`。这些文本列只写不读(终稿由 finalize 重新 interpret),故列名语义临时错位无碍,留待 #25 重做 state.db 时一并清理。**不需要 DDL / test_state_db 迁移 / 新 ADR。**

### 4.5 `src/notifiers/telegram_polling.py`
- `_make_card_messages`:body 区块从"💬一句话/🛠对你/⚡️锐评"三段 → 单段 body;cover 保留(标题+元信息);**卡片也去 emoji 装饰**(与 SOP 推送一致)。`_clip` 仍用于 body 限长。

## 5. 渲染（`src/pipeline/publish.py`）

- **全程无 emoji**(删 🏆📚📊📬🗂🏠🧭 与 `↳`、`` `[score]` `` 装饰)。
- `_render_must_read`:
  ```
  ## 今日必读

  ### 1. {title}
  {body}

  类型 {label} · 来源 [{source}]({link}) · {score} 分
  #tag #tag #tag
  依据:{claim}…(若有)
  ```
- `group_by_category`:**剔除必读条目**(必读=`select_must_read` 选中的;categories 只含其余),空类目不出。
- `_render_categories` → 「其余」:`- {title} — {label} · [{source}]({link}) · {score} 分`(一行,不挂 body,不挂 tags)。
- **删 `_render_overview`** 及其调用(数据概览整块移除)。
- 页脚:`---\nRSS · 历史归档 · 主站 ｜ AI News Daily`(无 emoji)。
- 草稿水印:`config.pending_watermark` 改成朴素"草稿待定稿"(去 ⚠)。
- front matter 不变(tags=类目 label;summary=daily_take[:140])。

## 6. 测试（TDD,先红后绿）

- `interpret`:golden 改新 schema(产 title/body/tags/evidence;回退产 body;eligible 用 body)。
- `publish`:snapshot 重录(无 emoji、必读成段、其余一行不重复、无数据概览、新页脚);`group_by_category` 剔除必读的 contract。
- `review`/`selfcheck`/`tick`:contract 迁到 `body`(`state_db` 不变,schema 不动)。
- `test_prompts`:校验 interpret_item.md 含新字段、不含旧字段。
- 全量 `uv run pytest` 绿 + `ruff check . && ruff format --check .`。

## 7. 实现拆分（writing-plans 细化）

1. 数据模型 + prompt(types `body`、interpret_item.md)。
2. interpret 产 body + eligible + 回退 + daily_take 输入(+golden)。
3. review + selfcheck 迁 body(+contract)。
4. tick 卡片(body 存入现有 summary_zh 列,schema 不动)。
5. telegram 卡片渲染去格子去 emoji(+golden)。
6. publish 渲染重写:无 emoji/必读成段/其余去重一行/删概览/新页脚(+snapshot)。
7. 全量回归 + lint。

## 8. 验收

打开站点当天日报:无 emoji、必读为"标题+成段"、其余一行且不重复必读、无数据概览、页脚朴素、每条 3 tags;Telegram 卡片同风格;全量测试绿。
