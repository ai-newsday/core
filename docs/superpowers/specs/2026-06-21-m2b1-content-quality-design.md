# M2-B1 设计 — 内容质量：AI 相关性 + 垃圾过滤 + body 截断修复

- 日期:2026-06-21
- 来源:M2-B brainstorm(Q1=C 信号闸+地板 属 M2-B2;Q2=C 词边界+LLM 兜底 属本 spec)
- 状态:设计待用户复审 → writing-plans

## 1. 目标

修掉日报里**当下可见的内容质量问题**(M2-A 上线后暴露):
1. **非 AI 内容混入**:HN 关键词过滤用裸子串,`ai` 命中 "br**ai**n",把 "Your brain was never designed for this much bad news" 这类非 AI 文章放进来。
2. **垃圾空壳条目**:无实质内容(如 "原始信息缺失,无法提供概述")仍进正刊。
3. **body 截断成病句**:body 撞 `body_max_chars` 被齐头硬切(如 "…这对机器人从业者意味着,可")。

**不在本 spec(留 M2-B2):** 配额提到 10/11、信号闸压 firehose 噪声、per-genre 质量地板。本 spec 只改"内容对不对/完不完整",不改"选几条/配额"。

## 2. AI 相关性 — 两层(Q2=C)

### 2.1 适配器层:关键词改词边界(便宜预过滤)
`src/adapters/sources/hn.py` 的关键词匹配 `if kws and not any(k in haystack for k in kws)` 是裸子串。改为**词边界**匹配:
- 新增纯函数 `_kw_match(haystack: str, keywords: list[str]) -> bool`:对每个关键词,若是单词(字母数字),用 `re.search(r"\b" + re.escape(kw) + r"\b", haystack)`;含空格的短语(如 "machine learning"/"open source")仍按子串(短语不会误伤)。命中任一即 True。
- `re.escape` 防关键词里的正则元字符。`\b` 对中文无意义但本表全英文,安全。
- 仅 hn.py 需要(reddit 无关键词过滤,靠 AI 子版 + min_score;rss 源是定向 AI 源)。

### 2.2 interpret 层:LLM 相关性兜底(权威)
`InterpretedItem` 加字段 **`relevant: bool = True`**。interpret 的 LLM 同时判定:
- prompt 加要求:`relevant` = 该条**既是 AI/ML 相关、又有可写的真实内容**则 true;**非 AI**(关键词漏过的 "model/agent" 语境误放)或**空壳无内容**(原文缺失/无法概述)则 false。
- 输出 JSON 加 `"relevant": true|false`。
- `build_ok_item`:`relevant = bool(parsed.get("relevant", True))`(缺省 true,向后兼容)。
- `extractive_fallback`:`relevant=True`(LLM 失败时不误杀;回退条目质量另由分数/地板把关)。

### 2.3 过滤生效两处(都读 `it.relevant`)
- **collect 发卡**(`src/pipeline/tick.py run_collect_tick`):`relevant==False` 的条目**不发审稿卡**(不打扰用户;截图那条就不该发)。
- **publish 渲染**(`src/pipeline/publish.py build_report`):`build_report` 过滤掉 `relevant==False`(与现有 `score >= min_display_score` 并列)。

> 注:interpret 在配额选取**之后**跑,非 AI 条目会先占坑再被踢,有少量配额浪费;但 §2.1 词边界已把聚合器噪声大幅压低,残留很少,可接受。彻底的前置过滤留 M2-B2 视情况再说。

## 3. body 截断修病句(interpret)

`src/pipeline/interpret.py` 新增纯函数 `_trim_to_sentence(text: str, n: int) -> str`:
- `len(text) <= n` → 原样返回。
- 否则在 `text[:n]` 内找最后一个句末标点(。！？；以及英文 . ! ?)的位置,截到该标点(含)为止。
- 若 `text[:n]` 内无任何句末标点 → 退回 `text[: n-1] + "…"`(硬切兜底)。
`build_ok_item` 与 `extractive_fallback` 里 `body = ...[: config.body_max_chars]` 改为 `body = _trim_to_sentence(..., config.body_max_chars)`。

## 4. 数据流 / 影响面

- `src/core/types.py`:`InterpretedItem` 加 `relevant: bool = True`(ReviewedItem 自动继承)。
- `src/prompts/interpret_item.md`:加 `relevant` 约束 + 输出 JSON 加 `relevant`。
- `src/pipeline/interpret.py`:`build_ok_item`(读 relevant + `_trim_to_sentence`)、`extractive_fallback`(relevant=True + trim)、新增 `_trim_to_sentence`。
- `src/adapters/sources/hn.py`:`_kw_match` 词边界。
- `src/pipeline/tick.py`:`run_collect_tick` 跳过 `relevant==False` 不发卡。
- `src/pipeline/publish.py`:`build_report` 过滤 `relevant==False`。

## 5. 测试(TDD)

- `hn` adapter:`_kw_match` 单测——`ai` 不命中 "brain"/"again";命中 "AI model released"/"new LLM";短语 "machine learning" 命中。一条"含 ai 子串但非 AI"(brain)的 fixture 被过滤。
- `interpret`:golden 加 `relevant` 字段;`relevant=false` 的 LLM 输出 → item.relevant False;缺省 → True;回退 → True。`_trim_to_sentence` 单测(超长截到句末、无句号硬切+省略、未超原样)。
- `tick`:`run_collect_tick` 对 `relevant=False` 的条目不调 `send_review_card`(用 FakeNotifier 断言 sent_cards 不含它)。
- `publish`:`build_report` 过滤 `relevant=False`(snapshot/contract:非相关条目不出现在渲染)。
- 全量 `uv run pytest` 绿 + `ruff check . && ruff format --check .`。

## 6. 实现拆分(writing-plans 细化)

1. `relevant` 字段 + prompt(types + interpret_item.md)。
2. interpret 产 relevant + `_trim_to_sentence`(+golden/unit)。
3. hn `_kw_match` 词边界(+unit)。
4. tick 发卡过滤 + publish 渲染过滤 relevant(+contract)。
5. 全量回归 + lint。

## 7. 验收

- HN 的 "brain/bad news" 类非 AI 条目不再进流水线(词边界);残留非 AI/空壳被 LLM `relevant=false` 挡在卡片与正刊之外;长 body 不再断成病句。全量测试绿。
