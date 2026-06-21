# M2-B1 Implementation Plan — 内容质量（AI 相关性 + 垃圾过滤 + body 截断修复）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让非 AI 内容(关键词裸子串误放的 "brain")、垃圾空壳条目不再进卡片/正刊,且 body 不再被截成病句。

**Architecture:** ① HN adapter 关键词改词边界纯函数;② `InterpretedItem` 加 `relevant: bool`,interpret 的 LLM 判定 AI 相关+有内容;③ collect 发卡 与 publish 渲染 都过滤 `relevant==False`;④ interpret 用句子感知截断替换 body 硬切。全 TDD。

**Tech Stack:** Python 3.12, pytest, pydantic, ruff, re。

**设计依据:** `docs/superpowers/specs/2026-06-21-m2b1-content-quality-design.md`。**不含**配额/信号闸(M2-B2)。

---

## 文件结构

- Modify: `src/core/types.py` — `InterpretedItem` 加 `relevant: bool = True`
- Modify: `src/prompts/interpret_item.md` — 加 `relevant` 约束 + 输出 JSON
- Modify: `src/pipeline/interpret.py` — `build_ok_item`(读 relevant + 句子截断)、`extractive_fallback`、新增 `_trim_to_sentence`
- Modify: `src/adapters/sources/hn.py` — 新增 `_kw_match` 词边界,替换裸子串
- Modify: `src/pipeline/tick.py` — `run_collect_tick` 跳过 `relevant==False`
- Modify: `src/pipeline/publish.py` — `build_report` 过滤 `relevant==False`
- Test: `tests/golden/test_interpret.py`, `tests/contract/test_hn_source.py`(或现有 hn 测试), `tests/contract/test_tick_decisions.py`, `tests/golden/test_publish.py`

---

## Task 1: `relevant` 字段 + prompt

**Files:** Modify `src/core/types.py`, `src/prompts/interpret_item.md`; Test `tests/contract/test_prompts.py`.

- [ ] **Step 1: 失败测试**(追加 `tests/contract/test_prompts.py`)

```python
def test_interpret_prompt_has_relevant_field():
    from src.core.prompts import load_prompt
    t = load_prompt("src/prompts/interpret_item.md")
    assert "relevant" in t
    assert '"relevant"' in t
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest tests/contract/test_prompts.py::test_interpret_prompt_has_relevant_field -v`
Expected: FAIL

- [ ] **Step 3: 改 `src/core/types.py`**

`InterpretedItem` 加字段(放在 `tags` 上面或 `eligible_for_must_read` 附近):
```python
    relevant: bool = True  # LLM 判定: 是否 AI/ML 相关且有真实内容; False → 不进卡片/正刊
```

- [ ] **Step 4: 改 `src/prompts/interpret_item.md`**

在硬约束区加一条,并在输出 JSON 加 `relevant`:
```
- `relevant`：布尔值。该条目**既与 AI/机器学习相关、又有可写的真实内容**时为 true；若**与 AI 无关**（例如只是恰好含 "model/agent" 等词的非 AI 文章），或**没有实质内容**（原文缺失、无法概述），则为 false。
```
输出 JSON 行改为:
```
{"title": "...", "body": "...", "tags": ["#x", "#y", "#z"], "evidence": [{"claim": "...", "anchor": "..."}], "relevant": true}
```

- [ ] **Step 5: 跑确认通过**

Run: `uv run pytest tests/contract/test_prompts.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/core/types.py src/prompts/interpret_item.md tests/contract/test_prompts.py
git commit -m "feat(types,prompt): add relevant flag (AI-related + has content) to interpret"
```

---

## Task 2: interpret 产 relevant + 句子感知截断

**Files:** Modify `src/pipeline/interpret.py`; Test `tests/contract/test_interpret_unit.py`, `tests/golden/test_interpret.py`.

- [ ] **Step 1: 失败测试**(追加 `tests/contract/test_interpret_unit.py`)

```python
def test_trim_to_sentence_cuts_at_punctuation():
    from src.pipeline.interpret import _trim_to_sentence
    # 超长 → 截到上限前最后一个句末标点(含)
    assert _trim_to_sentence("第一句。第二句很长很长很长。", 6) == "第一句。"
    # 未超 → 原样
    assert _trim_to_sentence("短句。", 50) == "短句。"
    # 无句末标点 → 硬切 + 省略
    assert _trim_to_sentence("没有标点的很长一段文字内容", 5) == "没有标点…"


def test_build_ok_item_reads_relevant_and_defaults_true():
    from src.core.types import InterpretConfig
    from src.pipeline.interpret import build_ok_item
    from tests.fakes import make_scored_item  # 若无此 helper, 用现有构造方式
    cfg = InterpretConfig()
    it = make_scored_item(link="https://x/1")
    parsed = {"title": "T", "body": "正文。", "tags": ["#a", "#b", "#c"],
              "evidence": [{"claim": "c", "anchor": "https://x/1"}], "relevant": False}
    out = build_ok_item(parsed, it, cfg)
    assert out.relevant is False
    parsed.pop("relevant")
    assert build_ok_item(parsed, it, cfg).relevant is True  # 缺省 True
```
> 若 `tests/fakes` 无 `make_scored_item`,改用本仓库 test_interpret_unit.py 现有的 ScoredItem 构造法(grep 该文件看它怎么造 item),保持 anchor=link、tags 恰好 3。

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest tests/contract/test_interpret_unit.py -k "trim_to_sentence or reads_relevant" -v`
Expected: FAIL

- [ ] **Step 3: 改 `src/pipeline/interpret.py`**

新增纯函数(放在 build_ok_item 上面):
```python
_SENT_ENDS = "。！？!?；;."


def _trim_to_sentence(text: str, n: int) -> str:
    """超长则截到上限内最后一个句末标点(含); 无标点则硬切 + 省略号。"""
    if len(text) <= n:
        return text
    window = text[:n]
    cut = max((window.rfind(ch) for ch in _SENT_ENDS), default=-1)
    if cut >= 0:
        return window[: cut + 1]
    return text[: n - 1] + "…"
```
`build_ok_item`:`body` 行改用句子截断,并读 `relevant`:
```python
    body = _trim_to_sentence(str(parsed.get("body", "")), config.body_max_chars)
    relevant = bool(parsed.get("relevant", True))
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
        relevant=relevant,
    )
```
`extractive_fallback`:body 用句子截断,relevant=True:
```python
    return InterpretedItem(
        **item.model_dump(),
        title=item.title_en,
        body=_trim_to_sentence(item.raw_summary or "", config.body_max_chars),
        tags=[],
        evidence=[],
        interpretation_status="extractive_fallback",
        eligible_for_must_read=False,
        relevant=True,
    )
```

- [ ] **Step 4: 改 golden `tests/golden/test_interpret.py`**

`grep -n "body\|relevant" tests/golden/test_interpret.py` —— 给 fake LLM 的 ok JSON 加 `"relevant": true`(保持现有用例意图);加一个用例:LLM 返回 `"relevant": false` → `item.relevant is False`。

- [ ] **Step 5: 跑确认通过**

Run: `uv run pytest tests/contract/test_interpret_unit.py tests/golden/test_interpret.py -v`
Expected: PASS

- [ ] **Step 6: lint + 提交**

Run: `uv run ruff check src/pipeline/interpret.py tests/contract/test_interpret_unit.py tests/golden/test_interpret.py` (format if needed)
```bash
git add src/pipeline/interpret.py tests/contract/test_interpret_unit.py tests/golden/test_interpret.py
git commit -m "feat(interpret): read relevant flag; sentence-aware body trim (no mid-sentence cut)"
```

---

## Task 3: HN 关键词词边界匹配

**Files:** Modify `src/adapters/sources/hn.py`; Test the HN adapter test (`grep -rln "HNAdapter\|hackernews\|hn" tests/` → likely `tests/contract/test_hn_source.py`).

- [ ] **Step 1: 失败测试**(追加到 HN adapter 测试文件)

```python
def test_kw_match_word_boundary():
    from src.adapters.sources.hn import _kw_match
    kws = ["ai", "llm", "machine learning"]
    # 子串误放被挡: "ai" 不命中 "brain"
    assert _kw_match("your brain was never designed for this", kws) is False
    # 真命中
    assert _kw_match("new ai model released", kws) is True
    assert _kw_match("a fast llm runtime", kws) is True
    # 短语命中
    assert _kw_match("intro to machine learning", kws) is True
    # 空关键词 → True(不过滤)
    assert _kw_match("anything", []) is True
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest -k kw_match_word_boundary -v`
Expected: FAIL（`_kw_match` 不存在）

- [ ] **Step 3: 改 `src/adapters/sources/hn.py`**

文件顶部 `import re`。新增纯函数:
```python
def _kw_match(haystack: str, keywords: list[str]) -> bool:
    """关键词命中判定。单词用词边界(\\b) 防子串误放(ai∉brain); 含空格的短语按子串。
    空关键词表 → True(不过滤)。"""
    if not keywords:
        return True
    hay = haystack.lower()
    for kw in keywords:
        k = kw.lower()
        if " " in k:
            if k in hay:
                return True
        elif re.search(r"\b" + re.escape(k) + r"\b", hay):
            return True
    return False
```
在 `fetch` 里把 `kws = [k.lower() ...]` + `if kws and not any(k in haystack for k in kws): continue` 替换为:
```python
        if not _kw_match(haystack, source.keywords or []):
            continue
```
(删掉原来的 `kws = [...]` 行;`haystack = f"{title} {url or ''}".lower()` 保留 —— `_kw_match` 内部再 lower 也无妨,或直接传 `f"{title} {url or ''}"`。)

- [ ] **Step 4: 跑确认通过**

Run: `uv run pytest -k "kw_match or hn" -v`
Expected: PASS（含 HN adapter 既有用例）

- [ ] **Step 5: 提交**

```bash
git add src/adapters/sources/hn.py tests/
git commit -m "fix(hn): word-boundary keyword match (ai no longer matches brain)"
```

---

## Task 4: collect 发卡过滤 + publish 渲染过滤

**Files:** Modify `src/pipeline/tick.py`, `src/pipeline/publish.py`; Test `tests/contract/test_tick_decisions.py`, `tests/golden/test_publish.py`.

- [ ] **Step 1: 失败测试 — tick 不发非相关卡**(追加 `tests/contract/test_tick_decisions.py`)

```python
def test_collect_skips_non_relevant_cards(tmp_path):
    async def go():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        ok = _item("https://x/ok", "AI thing")          # relevant 默认 True
        junk = _item("https://x/junk", "Not AI")
        junk = junk.model_copy(update={"relevant": False})
        notifier = FakeNotifier()
        await run_collect_tick("r1", NOW, [ok, junk], "take", db, [notifier])
        sent_links = [card.get("link") for _id, card in notifier.sent_cards]
        assert "https://x/ok" in sent_links
        assert "https://x/junk" not in sent_links
    asyncio.run(go())
```
> `_item(...)` 是该文件已有 helper(Task 已迁 body);若它不接受/构造 relevant,默认 True 即可,本用例用 `model_copy(update={"relevant": False})` 翻转。card dict 的 link 键名以 `_build_card` 实际输出为准(`"link"`).

- [ ] **Step 2: 跑确认失败**

Run: `uv run pytest tests/contract/test_tick_decisions.py::test_collect_skips_non_relevant_cards -v`
Expected: FAIL（junk 也发了卡）

- [ ] **Step 3: 改 `src/pipeline/tick.py`**

`run_collect_tick` 的 `for item in interpreted_items:` 循环体**第一行**加:
```python
        if not item.relevant:
            continue
```

- [ ] **Step 4: 失败测试 — publish 不渲染非相关**(追加 `tests/golden/test_publish.py`)

```python
def test_build_report_filters_non_relevant():
    # 用本文件既有的 ReviewedItem 构造法(_ri 之类), 造两条同分≥60: 一条 relevant=True 一条 False
    from src.pipeline.publish import build_report
    from src.core.types import PublishConfig, ReviewResult
    ok = _ri(link="https://x/ok", title="AI 条目", score=80)
    junk = _ri(link="https://x/junk", title="非 AI", score=80).model_copy(update={"relevant": False})
    rep = build_report(ReviewResult(reviewed_items=[ok, junk], daily_take="t", is_pending=False), "2026-06-21", PublishConfig())
    titles = [it.title for cat in rep.categories for it in cat.items]
    assert "AI 条目" in titles
    assert "非 AI" not in titles
```
> `_ri`/`ReviewResult` 构造以本文件现有写法为准;若 `ReviewResult` 字段不同,按实际签名构造(关键: 两条 reviewed_items, 一条 relevant=False)。

- [ ] **Step 5: 跑确认失败**

Run: `uv run pytest tests/golden/test_publish.py::test_build_report_filters_non_relevant -v`
Expected: FAIL

- [ ] **Step 6: 改 `src/pipeline/publish.py`**

`build_report` 的过滤行加 `and it.relevant`:
```python
    items = [
        it for it in review_result.reviewed_items
        if it.score >= config.min_display_score and it.relevant
    ]
```

- [ ] **Step 7: 跑确认通过**

Run: `uv run pytest tests/contract/test_tick_decisions.py tests/golden/test_publish.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/pipeline/tick.py src/pipeline/publish.py tests/contract/test_tick_decisions.py tests/golden/test_publish.py
git commit -m "feat(tick,publish): filter non-relevant items from cards and report"
```

---

## Task 5: 全量回归 + lint

- [ ] **Step 1: 全量**

Run: `uv run pytest -q`
Expected: 全绿。任一红 → 多半是某 fixture 缺 `relevant` 默认值(应为 True,字段有默认所以一般不会);按报错定位修。

- [ ] **Step 2: lint(含 format --check, CI 查全仓库)**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean（有差异先 `uv run ruff format .`）。

- [ ] **Step 3: 提交(若有 format 改动)**

```bash
git add -A
git commit -m "style: ruff format after m2b1" || echo "nothing to format"
```

---

## Self-review（写计划时自查）

- **Spec 覆盖**:§2.1 词边界=T3;§2.2 relevant 字段+prompt=T1,interpret 产 relevant=T2;§2.3 两处过滤=T4(tick+publish);§3 句子截断=T2;§5 测试散在各 task + T5 回归。
- **类型一致**:`relevant: bool` 全程一致;`_trim_to_sentence(text, n)`、`_kw_match(haystack, keywords)` 签名在定义与调用处一致;`build_ok_item` 传 `relevant=relevant`。
- **占位**:golden/既有测试用"grep 定位 + 迁移规则"表达(迁移既有测试性质);新单测均给完整代码。
- **风险**:`_ri`/`_item`/`make_scored_item`/`ReviewResult` 构造以各测试文件现状为准(T2/T4 已注明按实际签名);`relevant` 默认 True 保证未显式设置的旧 fixture 不被误删。
