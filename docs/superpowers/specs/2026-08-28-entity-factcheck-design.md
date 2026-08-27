# 实体归属 fact-check 设计(2026-08-28 brainstorm)

> 源头:2026-08-27 已实锤的编造案例——`interpret_item.md` 把内部 source slug(`x-ai-company`)当"来源"喂给 LLM,LLM 把它联想成真实公司 "xAI",把 NovelAI 的推文写成"xAI发布"(已 SHIPPED #102,修的是这一个具体触发路径:source 字段泄漏)。这份 spec 解决的是**更一般化的风险**:LLM 对任何公司名/模型名的归属判断都可能在信息不足时"自信地编",不限于 source 字段这一种触发方式。

## 目标

LLM 对实体归属没把握时,**不自动改写、不阻塞发卡**,而是显式标记出来,让人在审阅时决定要不要采信/补全。

## 设计

### 1. `interpret_item.md` 新增 JSON 字段:`entity_confident: bool`

在现有 JSON 输出结构里加一个字段,不新增 LLM 调用。Prompt 新增规则(紧跟已有的"机构归属"硬约束段):

> - `entity_confident`:布尔值。当 `title`/`body` 里提到的公司名/模型名**能在原文摘要或链接里找到明确依据**时为 true;当你是靠"来源"字段的名字联想/推测得出的,或原文信息不足以确定具体是哪家公司/哪个型号时,为 false——**宁可标 false,也不要标 true 然后编一个名字**。

### 2. `interpret.py::build_ok_item` 消费该字段

```python
if not parsed.get("entity_confident", True):
    flags = [*base_flags, QualityFlag(
        code="entity_uncertain", severity="warn", field="title",
        message="模型/公司归属未完全确定, 请核实后再发布",
    )]
```

写入 `InterpretedItem.quality_flags`(**已存在的字段**,`src/core/types.py:235`,此前只有从未接入生产的 `selfcheck.py` 会填它——这次是它第一次在生产路径上被真正使用,不是新造机制)。`extractive_fallback` 路径不需要这个字段(本来就是抽取式原文,没有 LLM 生成的归属判断,不存在编造风险)。

### 3. 审阅卡片可见徽章

`src/pipeline/tick.py::_build_card` 和 `src/notifiers/telegram_polling.py::_make_card_message`,复用现有 `⚠️ [未解读]` 徽章的视觉模式:当 `quality_flags` 里有 `code == "entity_uncertain"` 的条目时,标题前加 `🔎 [待核实]` 前缀。跟"未解读"徽章可以共存(两者是独立判断,一条可以同时"未解读"+"待核实"或只中一个)。

### 4. 复用现有 `edit` 决策动作,不新造编辑机制

`src/pipeline/review.py`/`src/core/types.py:310` 已经支持 `action: "edit"` + `edits: dict` 覆盖具体字段(如 title/body)。用户看到"待核实"徽章后,在现有审阅流程里用 `edit` 动作补全/纠正实体名即可——**这份 spec 不新增任何 UI/交互面**,只是让现有机制第一次真正被这个场景触发。

## 数据流

```
interpret_item() 单次 LLM 调用产出 JSON(含 entity_confident)
  → build_ok_item() 读该字段, false 时追加 QualityFlag
  → InterpretedItem.quality_flags 携带到 review/tick
  → tick._build_card 渲染徽章
  → 用户 TG 审阅时看到徽章, 用已有 edit 动作纠正(或直接 keep/drop)
```

## 测试要点

- `interpret_item.md` prompt 渲染包含新字段说明(prompt 内容测试,字符串包含检查)。
- `build_ok_item`:`entity_confident=false` 时 `quality_flags` 含预期 `QualityFlag`;`entity_confident=true`(或字段缺失,兼容旧/异常 LLM 输出)时不追加。
- `extractive_fallback` 路径不受影响(不产出该字段,`quality_flags` 保持空)。
- 卡片渲染:含 `entity_uncertain` flag 时徽章正确显示;不含时不显示;跟 `extractive_fallback` 徽章共存场景。

## 不做什么

- 不做自动二次校验/搜索验证(如接 web search 核实公司名)——这是重得多的机制,且不在这次三合一范围内,后续如果人工审阅发现"待核实"命中率太低/太高需要调 prompt 措辞,再回来看要不要加。
- 不改变 `relevant=false` 的既有语义(entity_confident 是独立维度,不影响条目是否进候选)。
