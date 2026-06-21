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

**结构(2026-06-21 用户定稿,取消必读/其余分层):** 全是要读的,**统一一种样式,按分类排**。无 emoji、无序号、不写"类型"label、来源/链接/分数放每条最后。

- **全程无 emoji**(删 🏆📚📊📬🗂🏠🧭 与 `↳`、`` `[score]` `` 装饰)。
- **分数地板**:新增 `PublishConfig.min_display_score`(默认 **60**,读 `config/publish.yaml`)。`build_report` 先过滤 `score >= min_display_score` 的条目;低于的不渲染。某分类被过滤空 → 不出该节。
- **取消必读分层**:不再有 `## 今日必读` 节,不再用 `select_must_read` 选取做渲染。`DailyReport.must_read` 置 `[]`(字段保留避免类型大改,渲染不用);`eligible_for_must_read` 字段保留(作质量信号,不再驱动渲染)。
- **按分类渲染**(`_render_categories`),genre 顺序按 `genre_labels` 键序,空类目不出:
  ```
  ## {label}

  ### {title}
  {body}

  #tag #tag #tag
  来源 [{source}]({link}) · {score} 分
  ```
  (每条:`### 标题`(无序号) → 成段 body → tags 行 → 末行"来源 链接 · 分数";有 evidence 时"依据"行放 tags 与来源之间或省略——见 §7 micro。)
- **删 `_render_overview` / `select_must_read` / `build_overview` 调用**(数据概览移除;必读选取不再用于渲染)。
- 页脚:`---\nRSS · 历史归档 · 主站 ｜ AI News Daily`(无 emoji)。
- 草稿水印:`config.pending_watermark` 改成朴素"草稿待定稿"(去 ⚠)。
- front matter 不变(tags=类目 label;summary=daily_take[:140])。
- **Telegram 终稿**(`_make_final_message` + `run_finalize_tick` summary):去掉"必读 Y 篇",改"共 X 条"(X=渲染入选条数);不再传 `must_read_titles`/`must_read_count`。
- **"删了就不出"**:review 层 `apply_decision(drop)→None` 已保证被 drop 的条目到不了渲染;M2-A 不改此逻辑(保留)。

## 6. 测试（TDD,先红后绿）

- `interpret`:golden 改新 schema(产 title/body/tags/evidence;回退产 body;eligible 用 body)。
- `publish`:snapshot 重录(无 emoji、按分类、每条统一成段 body+tags+末行来源、无必读节、无数据概览、新页脚);分数地板过滤 contract(score<60 不渲染、空类目不出);终稿"共 X 条"无必读计数。
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
