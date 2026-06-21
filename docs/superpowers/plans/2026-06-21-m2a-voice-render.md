# M2-A Implementation Plan — 文风内容契约 + 渲染重做

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 `InterpretedItem` 的 `summary/takeaway/hot_take` 合并为单一 `body`,让 prompt 产钩子标题+一段顺读正文,并重写 publish 渲染(无 emoji、必读成段、其余一行不重复、删数据概览),实现 `references/editorial-and-format-sop.md`。

**Architecture:** 纵向字段合并:数据模型→prompt→interpret/review/selfcheck/tick/telegram→publish 逐层迁 `body`,渲染层去 emoji+重构页面结构。纯函数+golden/snapshot TDD,不动 DB schema。

**Tech Stack:** Python 3.12, pytest, pydantic, ruff。

**设计依据:** `docs/superpowers/specs/2026-06-21-m2a-voice-render-design.md`;编辑规范 `references/editorial-and-format-sop.md`。

**不在本计划:** 配额/数量/过滤(M2-B / 甲-3)。

---

## 文件结构

- Modify: `src/core/types.py` — `InterpretedItem`(summary/takeaway/hot_take→body);`InterpretConfig`/`ReviewConfig`(summary_max_chars→body_max_chars)
- Modify: `config/interpret.yaml` — summary_max_chars→body_max_chars
- Modify: `src/core/config.py` — `load_interpret_config` 读 body_max_chars
- Modify: `src/prompts/interpret_item.md` — 产 `{title, body, tags, evidence}`
- Modify: `src/pipeline/interpret.py` — build_ok_item/extractive_fallback/build_daily_prompt 用 body
- Modify: `src/pipeline/review.py` — EDITABLE_FIELDS/_gate/apply_decision 用 body
- Modify: `src/pipeline/selfcheck.py` + `src/prompts/selfcheck.md` — format_lint/whitelist/critic prompt 用 body
- Modify: `src/pipeline/tick.py` — `_build_card` 用 body;upsert 把 body 存入 summary_zh 列
- Modify: `src/notifiers/telegram_polling.py` — `_make_card_messages` body 单段、去 emoji 格子
- Modify: `src/pipeline/publish.py` + `config/publish.yaml` — 渲染重写、watermark 朴素
- Test: `tests/golden/test_interpret.py`, `tests/snapshot/test_publish.py`(若在 tests/golden 则同名), `tests/contract/test_*`(review/selfcheck/tick/prompts)

---

## Task 1: 数据模型 + 配置（body 字段）

**Files:** Modify `src/core/types.py`, `config/interpret.yaml`, `src/core/config.py`; Test `tests/contract/test_interpret_config.py`.

- [ ] **Step 1: 失败测试**（追加到 `tests/contract/test_interpret_config.py`）

```python
def test_interpret_config_has_body_max_chars(tmp_path):
    from src.core.config import load_interpret_config
    p = tmp_path / "interpret.yaml"
    p.write_text("body_max_chars: 200\n", encoding="utf-8")
    cfg = load_interpret_config(str(p))
    assert cfg.body_max_chars == 200


def test_interpreted_item_uses_body_not_old_fields():
    from src.core.types import InterpretedItem
    fields = InterpretedItem.model_fields
    assert "body" in fields
    assert "summary" not in fields
    assert "takeaway" not in fields
    assert "hot_take" not in fields
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_interpret_config.py -k "body" -v`
Expected: FAIL（无 body_max_chars / body 字段）

- [ ] **Step 3: 改 `src/core/types.py`**

`InterpretedItem`:删 `summary`/`takeaway`/`hot_take` 三行,加 `body`:
```python
class InterpretedItem(ScoredItem):
    title: str  # 中文钩子标题, ≤ title_max_chars; 术语保留英文原文
    body: str  # 一段顺读正文(事实→实用→可选判断); 回退时为抽取式原文
    tags: list[str] = Field(default_factory=list)  # 恰好 tags_count 个或回退时 []
    evidence: list[Evidence] = Field(default_factory=list)
    interpretation_status: str  # "ok" | "extractive_fallback"
    eligible_for_must_read: bool
    quality_flags: list[QualityFlag] = Field(default_factory=list)
```
`InterpretConfig`:把 `summary_max_chars: int = 120` 改名为 `body_max_chars: int = 240`。
`ReviewConfig`:把 `summary_max_chars: int = 120` 改名为 `body_max_chars: int = 240`。

- [ ] **Step 4: 改 `config/interpret.yaml`**

把 `summary_max_chars: 120 ...` 这一行改为:
```yaml
body_max_chars: 240                  # 顺读正文上限(clamp 安全网; prompt 目标 ≤180)
```

- [ ] **Step 5: 改 `src/core/config.py`**

在 `load_interpret_config` 里把读 `summary_max_chars` 的那行改为读 `body_max_chars`(默认 `d.body_max_chars`)。`load_review_config` 同理(若它读 summary_max_chars,改为 body_max_chars;否则 dataclass 默认即可)。用 `grep -n summary_max_chars src/core/config.py` 定位并替换全部。

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_interpret_config.py -k "body" -v`
Expected: PASS（注:其它依赖旧字段的测试此时会红,Task 2-6 修复;本步只看新用例绿 + `grep -rn "summary_max_chars" src` 为空）

- [ ] **Step 7: 提交**

```bash
git add src/core/types.py config/interpret.yaml src/core/config.py tests/contract/test_interpret_config.py
git commit -m "feat(types): InterpretedItem summary/takeaway/hot_take -> single body"
```

---

## Task 2: prompt + interpret 产 body（+golden）

**Files:** Modify `src/prompts/interpret_item.md`, `src/pipeline/interpret.py`; Test `tests/golden/test_interpret.py`, `tests/contract/test_prompts.py`.

- [ ] **Step 1: 改 prompt `src/prompts/interpret_item.md`**

把"硬约束"里 summary/takeaway/hot_take 三条替换为一条 body 约束,并改输出 JSON 结构:
```
- `title`：中文钩子标题，简洁可扫读，带数字/反差更佳，≤64 字；模型名/公司名/技术名保留英文原文。
- `body`：一段顺读中文正文，≤180 字。先讲清事实，再落到"对从业者意味着什么、能怎么用"，可选用一句克制的判断收尾。不要分点、不要"一句话/对你/锐评"之类标签，不堆形容词，不用 emoji。
- `tags`：恰好 3 个，每个以 # 开头。
- `evidence`：关键事实 → 原文锚点；anchor 只能取自下方 link 或 related_links，不得编造；无则空数组。

只输出 JSON（不要额外解释）：
{"title": "...", "body": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}]}
```
(条目信息那段不变。)

- [ ] **Step 2: 失败测试**（`tests/contract/test_prompts.py` 追加）

```python
def test_interpret_prompt_uses_body_schema():
    from src.core.prompts import load_prompt
    t = load_prompt("src/prompts/interpret_item.md")
    assert "`body`" in t
    assert '"body"' in t
    assert "takeaway" not in t
    assert "hot_take" not in t
```

- [ ] **Step 3: 改 `src/pipeline/interpret.py`**

`build_ok_item`:
```python
def build_ok_item(parsed: dict, item: ScoredItem, config: InterpretConfig) -> InterpretedItem:
    tags = parsed.get("tags")
    if not isinstance(tags, list) or len(tags) != config.tags_count:
        raise ValueError("tags count not met")
    title = str(parsed.get("title", ""))[: config.title_max_chars]
    body = str(parsed.get("body", ""))[: config.body_max_chars]
    evidence = _filter_evidence(parsed.get("evidence"), item)
    eligible = bool(body) and len(evidence) >= config.min_evidence
    return InterpretedItem(
        **item.model_dump(),
        title=title,
        body=body,
        tags=[str(t) for t in tags],
        evidence=evidence,
        interpretation_status="ok",
        eligible_for_must_read=eligible,
    )
```
`extractive_fallback`:
```python
def extractive_fallback(item: ScoredItem, config: InterpretConfig) -> InterpretedItem:
    return InterpretedItem(
        **item.model_dump(),
        title=item.title_en,
        body=(item.raw_summary or "")[: config.body_max_chars],
        tags=[],
        evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
    )
```
`build_daily_prompt`(用 title,去 summary):
```python
def build_daily_prompt(items: list[InterpretedItem], template: str) -> str:
    lines = []
    for it in items:
        title = it.title if it.interpretation_status == "ok" else it.title_en
        lines.append(f"- {title}")
    return template.replace("{{items}}", "\n".join(lines))
```

- [ ] **Step 4: 改 interpret golden `tests/golden/test_interpret.py`**

`grep -n "summary\|takeaway\|hot_take" tests/golden/test_interpret.py` 定位。把 fixture/断言里的:
- LLM fake 返回的 JSON `{"title","summary","takeaway","hot_take","tags","evidence"}` → `{"title","body","tags","evidence"}`。
- 断言 `it.summary/it.takeaway/it.hot_take` → `it.body`。
- eligible 断言:原来靠 takeaway 非空,现靠 body 非空(语义不变,改字段名)。
保持每个 golden 用例的**意图**不变(ok 产出、回退零编造、必读门、确定性),只迁字段。

- [ ] **Step 5: 跑测试**

Run: `uv run pytest tests/golden/test_interpret.py tests/contract/test_prompts.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/prompts/interpret_item.md src/pipeline/interpret.py tests/golden/test_interpret.py tests/contract/test_prompts.py
git commit -m "feat(interpret): produce flowing body (hook title + paragraph), drop 3-field grid"
```

---

## Task 3: review 迁 body（+contract）

**Files:** Modify `src/pipeline/review.py`; Test `tests/golden/` / `tests/contract/` review tests.

- [ ] **Step 1: 改 `src/pipeline/review.py`**

```python
EDITABLE_FIELDS = ("title", "body", "tags", "evidence")
```
`_gate` 改用 body:
```python
def _gate(status: str, evidence: list[Evidence], body: str, config: ReviewConfig) -> bool:
    return status == "ok" and len(evidence) >= config.min_evidence and body != ""
```
`apply_decision` 里 edit 分支:把 `base["summary"] = str(base["summary"])[: config.summary_max_chars]` 改为 `base["body"] = str(base["body"])[: config.body_max_chars]`;`_gate(...)` 调用第三参从 `base["takeaway"]` 改为 `base["body"]`。删除对 summary/takeaway/hot_take 的引用。

- [ ] **Step 2: 改 review 测试**

`grep -rln "summary\|takeaway\|hot_take" tests | xargs grep -l review` 找到 review 的 golden/contract,把 fixture 的 InterpretedItem 构造与断言从旧三字段迁到 `body`(edit 用例改 `edits={"body": "..."}`,门用例靠 body 非空)。

- [ ] **Step 3: 跑测试**

Run: `uv run pytest -k review -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/pipeline/review.py tests/
git commit -m "feat(review): editable/gate on body instead of summary/takeaway/hot_take"
```

---

## Task 4: selfcheck 迁 body（+prompt+contract）

**Files:** Modify `src/pipeline/selfcheck.py`, `src/prompts/selfcheck.md`; Test `tests/contract/test_selfcheck_*`.

- [ ] **Step 1: 改 `src/pipeline/selfcheck.py`**

`format_lint`:把检查 `item.summary` 超长那段改为 `item.body` vs `config.body_max_chars`(注:`SelfCheckConfig` 需有 body_max_chars——见下);删"必读条目缺 takeaway"那两行,改为:
```python
    if item.eligible_for_must_read and not item.body:
        warn("body", "必读条目缺 body")
```
白名单:
```python
_FIELD_WHITELIST = {"body", "title", "tags", "evidence"}
```
`build_critic_prompt` 的 repl:删 `{{summary}}/{{takeaway}}/{{hot_take}}`,加 `{{body}}: item.body`(保留 title/title_en/raw_summary/evidence)。
> 注:若 `SelfCheckConfig` 没有 `body_max_chars`/`title_max_chars`,`format_lint` 用的是 `config.title_max_chars`/`config.summary_max_chars`——`grep -n "class SelfCheckConfig" -A12 src/core/types.py` 确认其字段;把它的 `summary_max_chars` 同样改名 `body_max_chars`(默认 240),并改 config 加载与 `config/selfcheck.yaml`(若有该键)。

- [ ] **Step 2: 改 prompt `src/prompts/selfcheck.md`**

把"待检条目"里 summary/takeaway/hot_take 三行替换为单行 `- 正文 body：{{body}}`;两类检查的描述把"takeaway/summary/hot_take"改为"body";field 取值限定改为 `body | title | tags | evidence`;输出 JSON 示例的 field 改 `body`。

- [ ] **Step 3: 改 selfcheck 测试**

`grep -rln "takeaway\|hot_take\|summary" tests/contract/test_selfcheck_*` 把 fixture/断言迁 body(format_lint 缺 body 用例、critic prompt 含 {{body}} 用例、白名单 body)。

- [ ] **Step 4: 跑测试**

Run: `uv run pytest -k selfcheck -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/selfcheck.py src/prompts/selfcheck.md src/core/types.py src/core/config.py config/selfcheck.yaml tests/contract/test_selfcheck_*
git commit -m "feat(selfcheck): lint/critic on body; field whitelist -> body/title/tags/evidence"
```

---

## Task 5: tick 卡片 + Telegram 卡片渲染

**Files:** Modify `src/pipeline/tick.py`, `src/notifiers/telegram_polling.py`; Test `tests/contract/test_telegram_notifier.py`, `tests/contract/test_tick_*`.

- [ ] **Step 1: 改 `src/pipeline/tick.py` `_build_card`**

```python
def _build_card(item: InterpretedItem) -> dict:
    return {
        "title_zh": item.title,
        "title_en": item.title_en,
        "source_label": _genre_label(item.genre.value),
        "source": item.source,
        "link": item.link,
        "score": item.score,
        "signals": item.signals,
        "body": item.body,
        "tags": item.tags,
    }
```
`run_collect_tick` 里 `db.upsert_pending_review(...)` 调用:把 `summary_zh=item.summary, takeaway=item.takeaway, hot_take=item.hot_take` 改为 `summary_zh=item.body, takeaway="", hot_take=""`(schema 不动,body 存进 summary_zh 列)。

- [ ] **Step 2: 改 `src/notifiers/telegram_polling.py` `_make_card_messages`**

body 区块从三段格子改单段,去 emoji 装饰:
```python
    title_zh = esc(card.get("title_zh", ""))
    title_en = esc(card.get("title_en", ""))
    source_label = esc(card.get("source_label", ""))
    score = card.get("score", 0)
    source = esc(card.get("source", ""))
    link = card.get("link", "")
    sig_line = _fmt_signals(card.get("signals", {}))
    body = esc(_clip(card.get("body", "")))
    tags = " ".join(esc(str(t)) for t in card.get("tags", []))

    cover = (
        f"<b>[{source_label}]</b> {title_zh}\n"
        f"<i>{title_en}</i>\n\n"
        f"<b>{score}</b> 分" + (f" ｜ {sig_line}" if sig_line else "")
        + f'\n<a href="{esc(link)}">{source}</a>'
    )
    body_msg = body + (f"\n\n{tags}" if tags else "")
    return cover, body_msg
```
(`_clip` 保留;`_fmt_signals` 内部 emoji 是否去由 SOP——本任务把卡片正文/封面装饰 emoji 去掉,signals 的 👍🔥 可保留为数据符号,不强制。)

- [ ] **Step 3: 改测试**

`tests/contract/test_telegram_notifier.py`:`test_send_review_card_sends_two_messages` 的 card dict 把 `summary_zh/takeaway/hot_take` 换成 `body`+`tags`;断言两条消息照旧。`test_card_body_bounded_under_telegram_limit`/`test_card_cover_escapes_link_url` 的 card dict 同样改 `body`。`tests/contract/test_tick_decisions.py` 的 `_item` 若构造旧字段,改 body。

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/contract/test_telegram_notifier.py tests/contract/test_tick_decisions.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/tick.py src/notifiers/telegram_polling.py tests/contract/test_telegram_notifier.py tests/contract/test_tick_decisions.py
git commit -m "feat(tick,telegram): card = title + single body + tags (no 3-field grid)"
```

---

## Task 6: publish 渲染重写（无 emoji / 按分类统一 body / 分数地板 / 删必读分层+概览）

**Files:** Modify `src/pipeline/publish.py`, `src/core/types.py`, `src/core/config.py`, `config/publish.yaml`, `src/notifiers/telegram_polling.py`, `src/pipeline/tick.py`; Test `tests/golden/test_publish.py`, `tests/contract/test_telegram_notifier.py`。

- [ ] **Step 1: 改 `config/publish.yaml`**

`pending_watermark: "草稿待定稿"`（去 ⚠ 长句）。追加 `min_display_score: 60`(分数地板,低于不渲染)。`top_keywords` 行可留(数据概览删了不读,无害)。

- [ ] **Step 2: 改 `src/pipeline/publish.py`**

新增 `PublishConfig.min_display_score: int = 60`(types.py 加字段 + `load_publish_config` 读 `config/publish.yaml` 的 `min_display_score`)。

`group_by_category`(分组**全部**入选条目,不再剔除必读;签名不变):
```python
def group_by_category(items: list[ReviewedItem], config: PublishConfig) -> list[CategorySection]:
    order = list(config.genre_labels)
    seen: list[str] = []
    buckets: dict[str, list[ReviewedItem]] = {}
    for it in items:
        st = it.genre.value
        if st not in buckets:
            buckets[st] = []
            seen.append(st)
        buckets[st].append(it)
    def rank(st: str) -> tuple[int, int]:
        return (order.index(st), 0) if st in order else (len(order), seen.index(st))
    out: list[CategorySection] = []
    for st in sorted(seen, key=rank):
        out.append(CategorySection(genre=st, label=config.genre_labels.get(st, st), items=buckets[st]))
    return out
```
`build_report`:**分数地板过滤** + 取消必读/概览:
```python
def build_report(review_result: ReviewResult, date_label: str, config: PublishConfig) -> DailyReport:
    items = [it for it in review_result.reviewed_items if it.score >= config.min_display_score]
    return DailyReport(
        date_label=date_label,
        daily_take=review_result.daily_take,
        must_read=[],                                           # 取消必读分层(字段保留,渲染不用)
        categories=group_by_category(items, config),
        overview=Overview(genre_distribution={}, keywords=[]),  # 数据概览删,空占位
        is_pending=review_result.is_pending,
        item_count=len(items),
        explore_count=sum(1 for it in items if it.is_explore),
    )
```
(若 `Overview`/`DailyReport` 字段与上不符,按 types.py 现状对齐——能给 `overview` 设默认就设默认并删该行。)

`_render_categories`(按分类,每条统一:`### 标题`(无序号) → 成段 body → tags 行 → 依据(若有) → 末行来源·分数;无 emoji、无类型 label):
```python
def _render_categories(report: DailyReport) -> list[str]:
    lines: list[str] = []
    for cat in report.categories:
        lines.append(f"## {cat.label}")
        lines.append("")
        for it in cat.items:
            lines.append(f"### {it.title}")
            lines.append("")
            lines.append(it.body)
            lines.append("")
            if it.tags:
                lines.append(" ".join(it.tags))
            if it.evidence:
                ev = "；".join(f"[{e.claim}]({e.anchor})" for e in it.evidence)
                lines.append(f"依据：{ev}")
            lines.append(f"来源 [{it.source}]({it.link}) · {it.score} 分")
            lines.append("")
    return lines
```
**删** `_render_must_read`、`_render_overview` 函数及其调用,以及 `build_report` 里 `select_must_read`/`build_overview` 的使用(留作未用会被 ruff 标;一并删)。
`render_markdown`:
```python
def render_markdown(report: DailyReport, config: PublishConfig) -> str:
    lines: list[str] = [f"# AI Daily · {report.date_label}", ""]
    if report.is_pending:
        lines.append(f"> {config.pending_watermark}")
        lines.append("")
    if report.daily_take:
        lines.append(f"> **今日看点**：{report.daily_take}")
        lines.append("")
    lines += _render_categories(report)
    lines.append("---")
    lines.append("RSS · 历史归档 · 主站 ｜ AI News Daily")
    return "\n".join(lines)
```

- [ ] **Step 2b: Telegram 终稿去必读计数**

`src/notifiers/telegram_polling.py` `_make_final_message` 改为(去 must_read 行/标题循环):
```python
def _make_final_message(summary: dict) -> str:
    esc = html_lib.escape
    date_label = esc(str(summary.get("date_label", "")))
    item_count = summary.get("item_count", 0)
    url = str(summary.get("url", ""))
    lines = [f"<b>AI Daily · {date_label}</b>", f"共 {item_count} 条", ""]
    if url:
        lines.append(f'<a href="{esc(url)}">阅读全文 →</a>')
    return "\n".join(lines)
```
`src/pipeline/tick.py` `run_finalize_tick` 的 `summary` dict 去掉 `must_read_count`/`must_read_titles`(保留 date_label/item_count/url)。同步改 `tests/contract/test_telegram_notifier.py` 里 `_make_final_message` 的断言(不再断言必读计数/标题)。

- [ ] **Step 3: 改 snapshot `tests/golden/test_publish.py`**

`grep -n "snapshot\|🏆\|📚\|一句话\|group_by_category\|build_overview\|select_must_read\|must_read" tests/golden/test_publish.py` 定位:
- fixture 的 `ReviewedItem`/`InterpretedItem` 构造迁 `body`(去 summary/takeaway/hot_take)。fixture 里要有**跨分数**的条目(≥60 与 <60)以验地板。
- `group_by_category` 调用改单参 `(items, config)`;删 must_read 相关断言。
- 新增 contract:**score<60 的条目不出现在渲染**;某分类全 <60 → 不出该节。
- 删 overview 相关断言。
- 重录 markdown snapshot:确认无 emoji、无 `今日必读`/`其余` 字样、`## {分类}` 节标题、每条 `### 标题`(无序号)+成段 body+tags+末行 `来源 ... · 分`、无类型 label、页脚朴素。重录按本仓库 snapshot 机制(删旧 snapshot 让重生成,或更新内联期望串)。

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/golden/test_publish.py tests/contract/test_telegram_notifier.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/pipeline/publish.py src/core/types.py src/core/config.py config/publish.yaml src/notifiers/telegram_polling.py src/pipeline/tick.py tests/golden/test_publish.py tests/contract/test_telegram_notifier.py
git commit -m "feat(publish): category-grouped uniform render; score floor 60; drop must-read tier + overview"
```

---

## Task 7: 全量回归 + lint

- [ ] **Step 1: 全量**

Run: `uv run pytest -q`
Expected: 全绿。任一红:多半是某处仍引用旧字段——`grep -rn "\.summary\b\|\.takeaway\|\.hot_take\|summary_zh=item\.summary\|summary_max_chars" src tests`,按本计划同款迁 body 后再跑。

- [ ] **Step 2: lint（务必含 format --check，CT 查全仓库）**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean。有 format 差异先 `uv run ruff format .` 再 check。

- [ ] **Step 3: 干跑产物自查（可选,验观感）**

Run: `uv run python -m src.cli --dry-run --interpret --registry tests/golden/data/registry_min.yaml 2>/dev/null | head` —— 确认 body 字段产出、无旧字段。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "test(m2a): full green after body migration + render rewrite"
```

---

## Self-review（写计划时自查）

- **Spec 覆盖**:§2 数据模型=T1;§3 prompt=T2;§4.1 interpret=T2,§4.2 review=T3,§4.3 selfcheck=T4,§4.4 tick(body→summary_zh)=T5,§4.5 telegram=T5;§5 渲染=T6;§6 测试散落各 task + T7 回归。
- **类型一致**:全程 `body`(非 summary/takeaway/hot_take);`body_max_chars`(InterpretConfig/ReviewConfig/SelfCheckConfig 一致);`group_by_category(items, must_read, config)` 新签名在 T6 内自洽。
- **占位**:golden/snapshot 的具体 fixture 行号未写死(用 grep 定位 + 迁字段指令),因各测试文件内容多变;这是"迁移既有测试"性质,非新写,允许以"定位+迁移规则"表达,但迁移规则具体(字段名映射明确)。
- **风险**:`DailyReport`/`Overview`/`CategorySection`/`SelfCheckConfig` 的确切字段需执行时按 types.py 现状对齐(T6 已注明 overview 字段处理两条路);`_fmt_signals` 的 👍🔥 是否去留 SOP 未强制,T5 默认保留(数据符号非装饰)。
