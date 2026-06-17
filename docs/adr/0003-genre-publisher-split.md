# ADR 0003 — 把 source_type 拆成 genre + publisher

- 日期:2026-06-17
- 状态:已采纳(子项目 #1 已实现,master 候选)
- 关联:[[paper-source-preference]] 诊断、设计 `docs/superpowers/specs/2026-06-17-source-taxonomy-genre-publisher-split-design.md`、计划 `docs/superpowers/plans/2026-06-17-source-taxonomy-genre-publisher-split.md`

## Context

2026-06-17 用真实 `--dry-run --score` 诊断源质量,确认输出条目弱的根因是**信号覆盖**,而非打分逻辑:只有 `hf-papers` 带热度信号,8 个输出槽里多数是零信号抽奖。诊断中暴露出更深的结构病:单一字段 `source_type`(paper/model/official/news/tool/blog/community)**同时编码了两个正交的轴**:

- "谁发的"(发布者权威):`official`/`tool`/`community` 本质是发布者标签。
- "发的是什么"(内容体裁):`paper`/`model`/`news` 是体裁,`blog` 半是体裁半是发布者。

后果:同一公司发的 news / changelog / release / 技术文章被压成一个权重;`langchain`(tool)与 `nvidia`(official)看似不同类、实则同体裁不同发布者;`release`(最高价值内容)在旧枚举里无对应值,从来收不进来。

## Decision

把 `source_type` 拆成两个正交字段,外加一层正交信号:

- **`genre`**(发的是什么)接管旧 `source_type` 的角色:驱动**配额**(每 genre 一个槽)+ 4 维内容价值(一手性/技术价值/产业影响/扩散潜力)。值:`paper, model, announcement, writeup, news`。
- **`publisher`**(谁发的)只驱动**机构影响力**。值:`lab, company, individual, media`。
- **signal**(多热)正交:原生(HF upvotes、GH stars、alphaXiv votes)+ 聚合器(HN/Reddit points)。

打分采用 **b1(按维度归属、单一权威标量)**:

```
机构影响力 = publisher_authority[publisher] + priority_bonus[priority]
一手性/技术价值/产业影响/扩散潜力 = genre_value[genre]
可见指标/时效/topic_boost/惩罚 = 不变
```

`config/scoring.yaml` 的 `dimension_scores`(按 type)拆成 `genre_value` + `publisher_authority`;`quota` 改挂 genre;`dedup.genre_rank`、`enrich.skip_genres`、`publish.genre_labels`、`QuotaLine.genre`、`CategorySection.genre`、`Overview.genre_distribution` 同步改名。

旧 7 个 type 的迁移:`paper`/`model` 平移为 genre;`news` → genre=news(+publisher=media);`blog` → genre=writeup(+publisher=individual);`official`/`tool`/`community` 不是 genre,溶解进 publisher,内容按端点重归 genre。两处有意重分类:`pytorch` news→announcement(官博非媒体)、`bair`/`stanford` paper→writeup(研究博文非论文)。

## Alternatives considered

- **(a) 二维查表** `dimension_scores[(genre, publisher)]`:逐字不变、改动最小,但只是把 1 维 key 换成 2 维 key,得不到"publisher 调一次对其所有 genre 生效"的真分离。否决。
- **(b2) 多维修正**:publisher 同时影响机构影响力 + 一手性。更贴语义但参数翻倍。地基阶段否决,留作 #4 调参时再评估。
- **保留 source_type、仅加源**:无法解决"同发布者多体裁"与"release 无对应值",且加更多一手源不产生信号。否决。

## Consequences

- 验收为 **output-stable**(非逐字不变):b1 让分数略变(如 `nvidia` 从旧 official 档落到 `company` 档,低于真正的 `lab`)。真实重跑确认 5 个 genre 配额各自合理填充,lab 公告作为独立 genre 浮现,无好内容被不可解释挤掉;287 测试绿。
- **延后项**:`release`/`changelog` 两个新 genre 与 `github_releases` adapter → 子项目 #2;HN/Reddit 信号富集(以及"个人 writeup 靠信号翻身")→ 子项目 #3;种子权重精调 → 子项目 #4。
- `aggregator` 暂不进 publisher 枚举(#3 若选信号富集方案则永不需要)。
