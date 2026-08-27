# 故事线合并设计(2026-08-28 brainstorm)

> 源头:用户 2026-08-27 反馈"A 公司发布模型,第三方平台各自发'已支持',现状被当独立新闻分别打分发布"。跟 KANBAN #73(故事线合并,原描述偏"多家媒体报同一事件不同措辞")、#70(同一信源时间线跟进挤占配额)是近亲但都覆盖不到这个模式:现有 dedup(`src/pipeline/dedup.py::cluster`)按标题/摘要**语义相似度**聚类,阈值 0.83,"A 发布/B 已支持"这类文字完全不像的条目不会被聚到一起。

## 目标

同一模型/产品的"原始发布"与"第三方支持"公告,在**最终发布**渲染时合并成一条,不再各占一个 genre 配额位。合并只发生在渲染层,**审阅阶段仍逐条独立展示**(用户明确要求,方便逐条判断真假/取舍)。

## 不做什么

- 不改动现有 `dedup.cluster()` 的语义相似度聚类(继续处理"多源报同一事件措辞不同")。故事线合并是**并行的第二种聚类信号**,不是替换。
- 不合并到审阅卡片层——TG 卡片保持一条推文/条目一张卡不变。
- 不做通用实体链接(NER 数据库、知识图谱一类)——只解决"同一天内提到相同型号/版本号 token"这一个具体模式。

## 架构

### 1. 新字段:`NewsItem.story_id: str | None = None`

加在 `NewsItem`(`src/core/types.py`,dedup 层基类),沿用 `cluster_id`/`related_links` 已有的"pydantic 继承自动透传"模式——`ScoredItem`/`InterpretedItem`/`ReviewedItem` 都是 `NewsItem` 的下游演进,不需要在每一层手动搬运字段。默认 `None`(不属于任何故事组,绝大多数条目)。

### 2. 新管线步骤:`src/pipeline/storylink.py::link_stories()`

位置:`score()` 之后、`interpret()` 之前(`src/cli.py::_collect_and_interpret`),对 `sres.selected_items`(已经是发卡池,量级小,几十条)操作。原因:候选实体判断不需要 interpret 产出的中文文案,用原始 `title_en`/`raw_summary` 即可,提前做还能让 interpret 复用 `story_id`(比如后续想在合并主条目 body 里提一句支持平台,不需要 interpret 知道合并结果——合并句由 publish 层拼接,interpret 不需要感知 story_id)。

```python
def link_stories(
    items: list[ScoredItem], llm, config: StoryLinkConfig, ctx: RunContext
) -> list[ScoredItem]:
    """纯函数(除 llm 调用外无副作用)。两阶段:
    1. 正则抓 entity token(型号名+版本号模式), 同一天内 token 重叠的两两配对成候选。
    2. 候选对过一次轻量 LLM 确认(是非题, 不产出新文字), 确认为真才连边。
    连通分量(并查集)各分配一个 story_id, 单例条目 story_id 保持 None。
    """
```

- **Token 抽取**(纯函数,可单测):正则 `[A-Z][A-Za-z0-9]*(?:[-\s]?\d+(?:\.\d+)*)+` 之类抓"字母前缀+数字版本号"模式(如 `GLM-5.3`, `v0.28.0`, `Llama 4`),对 `title_en` + `raw_summary` 前 N 字符跑,取所有命中 token 的规范化集合(大小写/连字符归一)。
- **候选配对**:同一天(`published_at` 同日,复用 `ctx.now` 所在的 date_label 窗口)内,任意两条目的 token 集合有交集 → 候选对。O(n²) 但 n 是发卡池量级(几十条),可接受。
- **LLM 确认**(新 prompt `src/prompts/story_link_confirm.md`):喂两条的 `title_en`+`raw_summary`(定长截断,复用 `interpret._trim_to_sentence` 风格),要求纯是非判断"是否在讲同一个模型/产品的同一轮动态",JSON 输出 `{"same_story": bool, "reason": "..."}`,**不允许 LLM 编写/复述实体名**,只判是非——避免复用今天已经修过的那类"LLM 命名实体"风险面。调用失败/解析失败 → 该候选对判 False(fail-closed,不误合并;宁可错过一次合并机会,不要把两个不相关条目错误拼一起给读者看)。
- **连通分量**:候选对确认为真的两两关系,用并查集合并成组,每组分配 `story_id = f"story-{date}-{n:03d}"`(跟 `cluster_id` 的 `evt-` 前缀风格对齐,换前缀区分)。

### 3. 新 config:`config/storylink.yaml` + `StoryLinkConfig`

跟 `release_importance`/`interpret` 同款多 provider 结构(`providers`/`models`/`fallback_models`/`temperature`/`max_tokens`/`timeout_s`),加:
- `entity_token_pattern: str`(正则,默认见上)
- `prompt_path: str = "src/prompts/story_link_confirm.md"`
- `enabled: bool = true`

### 4. 发布层合并:`src/pipeline/publish.py`

`build_report()` 里,在 `apply_quota()` **之前**插入合并(原因:合并应该先把同故事的条目收成一条再占配额,不然故事组可能因为占了多个配额位反而把其它公司当天的公告挤掉——跟 KANBAN #70 说的"挤占配额"是同一个毛病,合并要先于配额生效才能真正省位置):

```python
def merge_story_groups(items: list[ReviewedItem], max_support: int = 3) -> list[ReviewedItem]:
    """按 story_id 分组; 组内按 published_at 升序, 最早=原始发布(留作 primary);
    其余按 -score 排序取前 max_support 个当"已支持平台", 拼进 primary.body 末尾一句;
    组内其余条目(含超出 max_support 的部分)从结果中剔除, 不再单独占位。
    story_id 为 None 的条目原样透传。"""
```

- primary 的 body 追加一句克制表述,如:`\n\n目前已知 {A}、{B}、{C} 等平台跟进支持。`(具体措辞留给 interpret 那份 spec 的文风约束校对,或直接在这里用固定模板——不需要额外 LLM 调用)。
- 每个被提及的支持平台名**不能用 `item.source`(内部配置 slug)**——今天刚修过 source 泄漏喂给 LLM 当事实这个坑(#102),这里不能在渲染层重新踩一遍。对应的 `link` 仍然登记进 `_ref()` 参考表,保持"所有事实可点链接溯源"的现有约定。**具体显示名用什么留给实现阶段确认**(候选:该条目自己已经过 fact-check 的 `title` 里提取的实体词、或链接域名——两者都不是内部 slug,但哪个读起来更自然需要拿真实案例试),不是这份设计要锁死的架构决策。
- `PublishConfig` 新增 `story_merge_max_support: int = 3`。

### 5. 数据流小结

```
score()  →  sres.selected_items (发卡池, 无 story_id)
  → link_stories()  →  items 打上 story_id (原地风格, 组内共享同一个 id)
  → interpret()  →  InterpretedItem 继承 story_id (无需感知合并逻辑)
  → review()     →  ReviewedItem 继承 story_id (逐条独立审, 不受影响)
  → build_report():
       apply_adapter_quota() → apply_quota() 之前插入 merge_story_groups()
       (先合并让位, 再配额, 再分组渲染)
```

## 测试要点(留给实现阶段的 TDD 计划展开)

- `link_stories`:token 抽取纯函数单测(命中/不命中样例);候选配对逻辑(同天 token 交集);LLM 确认 mock 测试(true/false/异常三态);并查集分组正确性;`published_at` 跨天不配对。
- `merge_story_groups`:分组、最早优先、top-N 截断、body 拼接、被合并条目从结果剔除、`story_id=None` 透传不受影响。
- 端到端:真实 dry-run 前后对比(参考 [[verify-scoring-changes-against-real-dry-run]]),找一天真实数据验证有没有实际抓到"发布+支持"这类候选对。

## 待实现阶段确认的细节(非架构性,不阻塞设计通过)

- entity token 正则的具体模式需要拿真实数据反复调(设计阶段给的是示例,不是最终值)。
- 合并句的具体措辞模板,过一遍 `references/editorial-and-format-sop.md` 文风校对。
