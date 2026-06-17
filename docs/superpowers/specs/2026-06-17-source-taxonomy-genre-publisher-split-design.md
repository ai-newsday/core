# 设计:source_type 拆分为 genre + publisher(地基重构 / 子项目 #1)

- 日期:2026-06-17
- 状态:待评审
- 关联:源质量诊断(2026-06-17)、`docs/adr/`(本设计需配一篇 ADR,架构变更)

## 背景与问题

2026-06-17 用真实 `--dry-run --score` 诊断源质量,确认:打分逻辑没问题,根因是**信号覆盖** —— 只有 `hf-papers` 带热度信号,8 个输出槽里 6 个是零信号抽奖。更深一层的结构病:单一字段 `source_type`(paper/model/official/news/tool/blog/community)**同时编码了两个正交的轴**:

- "谁发的"(publisher 权威):official/tool/community 其实是 publisher 标签。
- "发的是什么"(content genre):paper/model/news 是 genre,blog 半是 genre 半是 publisher。

后果:同一公司发的 news / changelog / release / writeup 被压成一个权重;`langchain`(tool)和 `nvidia`(official)看似不同类,实则只是同 genre 不同 publisher 档;`release`(最高价值内容)在现有枚举里**根本没有对应值**,从来收不进来。

本设计是整个源质量重构的**地基子项目 #1**,只做 schema 拆分(behavior 层面 output-stable 的重构)。后续子项目:#2 `release` genre + `github_releases` adapter;#3 HN/Reddit 信号富集层;#4 用真实数据重新调参。

## 目标 / 非目标

**目标**
- 把 `source_type` 拆成两个正交字段:`genre`(发的是什么)+ `publisher`(谁发的)。
- 打分配置从"一张按 type 的表"拆成 `genre_value`(4 维内容价值)+ `publisher_authority`(机构影响力标量)。
- 配额从挂 `source_type` 改挂 `genre`。
- 验收标准:**output-stable** —— 重跑 `--dry-run --score`,选出的条目与重构前一致或仅有可解释的小位移;全部测试更新后转绿。

**非目标(明确推迟)**
- 不加 `release` / `changelog` genre(→ #2)。
- 不加 `aggregator` publisher、不接 HN/Reddit(→ #3)。
- 不做精确调参(种子值够用即可,精调 → #4)。
- 不做按 item 动态分 genre(本期按**源**赋一个固定 genre)。
- 不为"个人经验分享"做特殊配额机制 —— 该内容靠 #3 的信号层浮上来。

## 核心决策(brainstorm 结论)

1. **genre × publisher 怎么合进打分:选 (b1) 按维度归属、单一权威标量。**
   - `机构影响力 = publisher_authority[publisher] + priority_bonus`
   - 其余 4 维(一手性 / 技术价值 / 产业影响 / 扩散潜力)= `genre_value[genre]`
   - 选 (b1) 而非"二维查表 (a)":(a) 只是 1 维→2 维改名,得不到真分离。选单标量 (b1) 而非多维修正 (b2):地基阶段结构越简单越好,"一手性该不该受 publisher 影响"这种细调留给 #4。
2. **验收 output-stable 而非逐字不变**:(b1) 会让分数略变(如 nvidia 从 official 档降到 company 档),用重跑 diff 验证,而不是强求 byte-identical。
3. **个人经验分享走信号(a 方案)**:归入 `(writeup, individual)`,与公司 writeup 同槽竞争,靠 #3 的 HN/Reddit 信号翻盘;#1 不做特殊照顾。`publisher` 多样性配额约束(c 方案)记为 #3/#4 候选。
4. **HN/Reddit 是信号源不是 taxonomy 槽(B 方案)**:#1 的 publisher 枚举锁 4 个,不加 `aggregator`;纯站内自帖在 #3 直接跳过。

## Schema(`src/core/types.py`)

删除 `SourceType` 枚举,新增:

```python
class Genre(str, Enum):
    paper = "paper"
    model = "model"
    announcement = "announcement"   # 机构第一方公告/官博通告,一手性高
    writeup = "writeup"             # 技术文章/分析,一二手皆可,一手性中
    news = "news"                   # 第三方媒体报道,一手性低

class Publisher(str, Enum):
    lab = "lab"                     # 前沿研究机构
    company = "company"             # 厂商/工具/平台
    individual = "individual"       # 个人/独立作者
    media = "media"                 # 新闻媒体(对立、低权威)
```

字段改动:
- `RawItem`:`source_type: SourceType` → `genre: Genre` + `publisher: Publisher`。
- `SourceSpec`:`type: SourceType` → `genre: Genre` + `publisher: Publisher`。
- 下游同步改名:`QuotaLine.source_type` → `genre`;`CategorySection.source_type` → `genre`;`DedupConfig.source_type_rank` → `genre_rank`(见下)。`ScoredItem`/`InterpretedItem`/`ReviewedItem`/`NewsItem` 继承字段自动跟随。

## 打分配置(`config/scoring.yaml`)

`dimension_scores`(按 type)拆为两张表(数值为**种子值**,从现有 dimension_scores 推导,精调 → #4):

```yaml
genre_value:                       # genre 定 4 维内容价值
  paper:        {一手性: 20, 技术价值: 16, 产业影响: 8,  扩散潜力: 7}
  model:        {一手性: 18, 技术价值: 14, 产业影响: 10, 扩散潜力: 9}
  announcement: {一手性: 20, 技术价值: 10, 产业影响: 12, 扩散潜力: 9}
  writeup:      {一手性: 12, 技术价值: 12, 产业影响: 8,  扩散潜力: 9}
  news:         {一手性: 8,  技术价值: 6,  产业影响: 12, 扩散潜力: 11}

publisher_authority:               # publisher 定机构影响力(单标量)
  lab: 18
  company: 14
  individual: 8
  media: 12

priority_bonus: {1: 6, 2: 3, 3: 0, 4: -2, 5: -4}   # 保留,仍叠到机构影响力
priority_bonus_default: 0

quota: {paper: 2, announcement: 2, writeup: 2, model: 1, news: 1}   # =8, 改挂 genre
total_limit: 8
```

`recency` / `topic_boost` / `penalty` / `popularity_weights` / `popularity_cap` **全部不变**。打分公式:
`机构影响力 = publisher_authority[publisher] + priority_bonus[priority]`;`一手性/技术价值/产业影响/扩散潜力 = genre_value[genre]`;`可见指标 = sum(weight*sqrt(signal)) cap`(不变);加 时效 + topic_boost − penalty。

## 逐源映射(`config/sources.yaml`)

每行 `type:` → `genre:` + `publisher:`,`adapter`/`priority`/`max_items`/`status`/`url` 不动。

| genre | publisher | 源 |
|---|---|---|
| paper | company | hf-papers |
| paper | lab | nature-machine-intelligence |
| paper | company | papers-cool-{ai,cl,lg,cv}、arxiv-cs-{cl,lg,ai,cv}(均 status:manual,离线,赋值无关输出) |
| model | company | hf-models |
| announcement | lab | openai、deepmind、google-research、apple-ml、anthropic\*、mistral\*、ai2\*、meta-ai\* |
| announcement | company | nvidia、aws-ml、together-ai、huggingface-blog、microsoft-ai\*、**pytorch**(原误标 news,修正) |
| writeup | lab | bair、stanford-ai(研究博文,原 paper → 改 writeup) |
| writeup | company | langchain、ollama、comfy、replicate、roboflow、civitai、llamaindex\*、modal\* |
| writeup | individual | simonwillison、interconnects、import-ai、gwern、garymarcus、geohot、lcamtuf、sebastian-raschka、lilian-weng、eugene-yan、minimaxir、latent-space、dwarkesh、全部 hn-\* |
| news | media | the-decoder、venturebeat、techcrunch、mit-tech-review、ars-technica、theverge、qbitai\*、jiqizhixin\* |

(\* = 现 `status: manual`,不运行;仍补全字段保持 schema 完整)

**有意的重分类(非平移)**:
- `pytorch`:news → `(announcement, company)`(官博非媒体)。
- `bair` / `stanford-ai`:paper → `(writeup, lab)`(研究博文非论文)。

其余为语义平移。注意:旧 `community`(latent-space、dwarkesh)在新模型里溶解为 `(writeup, individual)`;旧 `official`/`tool`/`blog` 按上表分流。

## 下游 rekey

- **dedup 去重保留**(`src/pipeline/dedup.py` + `DedupConfig`):同一故事多源重复时保留哪条,从 `source_type_rank` 改为 `genre_rank: [paper, model, announcement, writeup, news]`(留更权威的内容形态);genre 相同时按 `publisher_authority` 高者优先,再回退现有 tie-break。
- **报告分组**(`src/pipeline/publish.py` + `CategorySection`/`Overview`):日报按 `genre` 分节,节标题用 genre 名。
- 所有读取 `config` 的入口(`src/core/config.py`、`src/cli.py`)按新 key 解析;阈值/权重/配额仍只从 `config/` 读,不写死(CLAUDE.md)。

## 错误处理 / 边界

- `genre` / `publisher` 用 Enum + pydantic 校验;`sources.yaml` 出现未知值 → 加载即报错(fail fast),不静默回退。
- `priority` 缺省走 `priority_bonus_default`(不变)。
- manual 源仍需合法 genre/publisher 字段,否则加载失败 —— 强制 schema 完整。

## 测试策略(TDD,先红后绿)

1. **契约/类型测试**:`Genre`/`Publisher` 枚举、`RawItem`/`SourceSpec` 新字段、未知值报错。
2. **打分纯函数测试**:给定 (genre, publisher, priority, signals),断言各维度分 = 新公式;覆盖 b1 拆分(机构影响力只来自 publisher+priority)。
3. **配额测试**:quota 按 genre 生效,和为 8。
4. **golden/snapshot 更新**:现有引用 `source_type` 的测试改到新 schema(更新非删除)。
5. **dedup/报告分组测试**:genre_rank 去重、按 genre 分节。
6. **集成验证(非自动断言)**:重构前后各跑一次真实 `--dry-run --score`,人工 diff `selected_items` 确认 output-stable。

CLAUDE.md 约束:contract/golden/snapshot CI 全绿才允许合并;不为"跑起来"绕过 schema 校验或删测试;`--dry-run` 必须可用。

## 实施顺序(给 writing-plans 的提示)

1. types.py 拆字段(枚举 + RawItem/SourceSpec)。
2. config.py / scoring.yaml 双表加载 + 打分公式改 b1。
3. sources.yaml 逐源映射。
4. dedup genre_rank、报告按 genre 分节、QuotaLine.genre。
5. 更新所有受影响测试 → 全绿。
6. 真实重跑 diff 验证 output-stable。
7. 更新 `docs/specs/` + 写 `docs/adr/` 决策记录。

## 风险

- 改动面广(凡引用 `source_type` 处),但都是机械改名 + 双表查表,无新算法。
- nvidia 等 company 源机构影响力较旧 official 下降一档 —— 预期且可接受(output-stable),靠重跑确认未把好条目挤掉。
- 种子值若偏差导致选择明显劣化,在 #4 调参修正,不在 #1 纠结数值。
