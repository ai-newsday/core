# 内容确定性打分 + 标题钩子设计(2026-08-28 brainstorm)

> 源头:KANBAN 三条已记录多时的问题,同属 `interpret_item.md` 没管住的输出质量,这次一起处理:
> 1. **内容确定性缺打分维度**——GPT-5.6 那条实锤:LLM 自己正文写"当前信息未披露具体技术细节或基准数据,需后续验证实际效果",却拿了 100 分(满分)。打分对"内容本身写没写清楚、信息够不够扎实"没有任何约束力。
> 2. **标题编造动作词**——同一实锤:原文标题是效率跟进文章("How GPT-5.6 fuses..."),真正发布是三周前,interpret 层却在标题写"OpenAI 发布 GPT-5.6"——凭空加了"发布"这个原文没有的动作词,直接违反 CLAUDE.md"宁可少写不可编造"。
> 3. **hf-papers 标题没把方法/模型名前置当钩子**——`references/editorial-and-format-sop.md` 已有标准样例("0.22B 反超 11.9B"),但实践里论文标题经常没做到,方法/模型名没放在最前面当钩子。

## 目标

- 内容含糊/自我怀疑的条目,在**发布排序**时天然靠后,不再无区分地拿满分。
- 标题动作词(发布/推出/开源等)必须有原文依据,没有就用中性表述。
- 论文类标题遵守 SOP 已锁定的"方法/模型名前置"钩子规则。

三条都是 `src/prompts/interpret_item.md` 的 prompt 约束改动 + 第一条额外牵涉 `score`/`score_breakdown` 的回写,不涉及新管线步骤,风险和复杂度都低于故事线合并/fact-check 两份 spec。

## 设计

### 1. 内容确定性 → 固定扣分

`interpret_item.md` JSON 输出新增 `content_certain: bool`,规则:

> - `content_certain`:布尔值。若 `body` 里出现"未披露/尚不明确/需后续验证/具体细节未知"这类自我保留表述,或原文摘要本身信息稀薄、只是预告性质,为 false;信息扎实、有具体数据/机制支撑时为 true。

**架构约束(已跟用户确认过)**:`score()` 在 `interpret()` **之前**跑(候选池已经定了),`content_certain` 没法改变一条内容能不能进候选池——但 `publish.py::build_report()` 在人工 keep 之后会**复用同一个 `apply_quota()` 纯函数按 `-score` 重新排序**,所以在 interpret 阶段回写 `score` 依然能在配额吃紧时把"内容确定性低"的条目挤到后面,不会否决用户已经做出的 keep 决定,只是降低它抢配额位的优先级。

`interpret.py::build_ok_item` 里:
```python
score = item.score
breakdown = dict(item.score_breakdown)
if not parsed.get("content_certain", True):
    score = max(0, score + config.uncertain_content_penalty)  # penalty 是负数
    breakdown["内容确定性"] = config.uncertain_content_penalty
```
`ScoringConfig`(`src/core/types.py`)新增 `uncertain_content_penalty: float = -15.0`,跟已有 `firehose_penalty: float = -20.0` 同款风格(config 可调的固定扣分,不是新的加权维度,不需要按 genre 分别调权重——这是 brainstorm 时已经跟用户对齐过的选择,对应 KANBAN #69 里"不是简单加一维"的顾虑)。

### 2. 标题动作词护栏

`interpret_item.md` 硬约束段新增:

> - `title` 里的动作词(发布/推出/开源/上线等)必须能在 `raw_summary` 或 `related_links` 里找到依据;找不到依据时改用中性表述(如"更新"/"介绍"/"谈"),不得凭"这类新闻通常怎么写"去猜一个动作词。

### 3. hf-papers 标题前置钩子

`interpret_item.md` 硬约束段新增(紧跟 `title` 那条规则,只对 `genre == paper` 生效,可以用条件文案或直接在模板里按 genre 分支——留给实现阶段决定用 prompt 内条件语句还是两份模板,不影响这份设计的架构结论):

> - 论文类条目:标题必须把论文提出的方法名/模型名放在最前面当钩子(参考"0.22B 反超 11.9B:Moebius 把 Image Inpainting 模型压到 2% 体量"这类结构:数字/反差 + 方法名 + 效果),不要用"研究者提出新方法"这类不带信息量的开头。

## 数据流

```
interpret_item() 单次 LLM 调用产出 JSON(含 content_certain, 以及受硬约束影响的 title/body 本身)
  → build_ok_item():
       - 用 content_certain 回写 score/score_breakdown
       - title 已经受动作词/钩子约束(prompt 层面, 不需要额外代码校验)
  → InterpretedItem.score 携带修正后的分数, 走 review → publish
  → build_report() 的 apply_quota() 复用这个分数重新排序
```

## 测试要点

- `build_ok_item`:`content_certain=false` 时 `score` 按 `uncertain_content_penalty` 扣减且不低于 0,`score_breakdown["内容确定性"]` 写入负值;`content_certain=true`(或字段缺失)不扣分,行为与改动前一致(向后兼容)。
- `extractive_fallback` 路径不受影响(不产出该字段)。
- prompt 内容测试:模板包含动作词护栏 + hf-papers 钩子规则的字符串断言。
- 回归:确认新增字段不破坏现有 `tests/golden/` 对 interpret 输出结构的既有断言(如有,需要同步更新 fixture)。

## 不做什么

- 不新增 genre 级别的权重维度(已跟用户对齐:固定扣分,不是新的加权分项)。
- 不对已发布历史内容回溯重新打分——只影响新增条目。
