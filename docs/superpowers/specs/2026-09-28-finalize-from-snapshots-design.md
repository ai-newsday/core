# A1 设计:定稿只处理保留条目(finalize from snapshots)

- 日期:2026-09-28 · 状态:设计已确认,待 plan
- 来源:2026-09-28 双轴审核(Boss 已验收)工程 E1,见 `docs/review/2026-09-26-evaluation.md` 末节
- 所属重构线:A1 → A2 → B → C(见 `docs/KANBAN.md` §3)

## 问题

`finalize` 在 `src/cli.py::run_tick` 里重跑一整遍 collect → enrich → dedup → score → storylink → interpret,
再在 `run_finalize_tick` 里按 item_id 对决策(`src/pipeline/tick.py:234`)。用户白天 keep 的条目若晚上
掉出新池(排名变化、源站抓取失败、解读失败),会被**悄悄丢掉**;同时夜间解读 44–60 条,绝大多数因没审过
被确认门挡掉,额度白烧。

## 已定决策(用户 2026-09-28)

1. **锁身份、刷内容**:定稿只处理 keep 条目,但晚上对它们重新解读(允许终版前修正)。
2. **重解读失败 → 跳过该条**,宁可少发;找不到快照同样跳过。
3. **快照存 state.db 新表**(不存 KV、不进 git)。
4. 确认门不变:未审不发。近期用户不审 → 接受空稿;A1 只能用测试 + 假决策 dry-run 验收。

## 数据流

**collect(每天 8 次)**:流程不变,推卡时把该条**解读前的 `ScoredItem`**(即 `interpret()` 的输入,
由 `run_tick` 以 `scored_items` 传给 `run_collect_tick`、按 link 对应;不是从解读结果里截字段——解读会把
内容确定性罚分叠进 `score`/`score_breakdown`,存它会让 finalize 重复扣分)整条 JSON 写入
`review_snapshots(item_id PK, date, snapshot_json, updated_at)`。`item_id` = 现有 `sha256(link)[:16]`。
同条重复采集 → 覆盖为最新快照,卡片不重推(与现状一致)。写快照失败只记日志,不影响推卡。

**finalize**:
1. 拉决策 → keep 的 item_id 列表。
2. 按 id 取快照;缺失 → 跳过(`no_snapshot`)。
3. 只对这些条目调 `interpret()`,**`cache=None` 绕过 36h 解读缓存,真正重新解读**(用户 2026-09-28 定);
   `interpretation_status != "ok"` → 跳过(`interpret_failed`),`relevant=False` → 跳过(`irrelevant`)。
   有快照的 **drop** 条目不解读,但照样参与 id→link 映射与反馈闭环(负反馈不能丢)。
4. 其余不变:跨期去重 → review → build_report → 标题/摘要重生成 → 配图 → render → mark_published → 通知。

### 决策只结算一次

决策 KV 保留 7 天、每晚全量拉回,快照不过期;不筛的话同一条 keep/drop 会每晚被重解读、反馈重复入账。
所以 finalize 只处理**新鲜决策**:该 id 在 `settled_decisions(item_id PK, date_label, ts)` 里的 label
等于本次 `date_label`(同日重跑放行),或 id 既未结算、也不在 `decisions` 表里(上线第一晚:旧 finalize
已记账的决策视为已结算)。判断在 `record_decisions` 写入之前做。处理完后本批新鲜决策(keep + drop,
含解读失败/无快照的 keep——不顺延)全部以本次 `date_label` 结算,首次 label 固定。
`finalize_summary.kept` 计的是新鲜 keep 数。代价:结算后在卡片上改主意(keep→drop)不再生效。

**删除**:finalize 分支的 collect / HN / release_importance / hf_readme / dedup / score / storylink 调用;
`finalize.yml` 的 x-signals clone 步骤。

## 错误处理与日志

- 每条跳过:`finalize_item_skipped{item_id, reason, error}`,reason ∈
  `no_snapshot | interpret_failed | irrelevant | already_published | model_mismatch | rule_cut`。
  - `already_published`:72h 跨期去重排除(`tick.py` `already_published_elsewhere`)。
  - `model_mismatch`:重解读落到备链模型,被"一期一模型"(`publish.py:200`)滤掉——此前无日志。
  - `rule_cut`:被组稿规则砍掉——keep 超过 `total_limit`(12)的配额、GitHub 上限、分数地板(`publish.py:209-233`)——此前无日志。
  - 只加日志,**不改筛选行为**。
- 收尾:`finalize_summary{kept, published, skipped_by_reason}`。

## 超额规则(用户 2026-09-28 定)

- keep 超过 12 条:**不顺延**,按现有规则砍(X 保底 4 → 超额时按 genre 配额取高分)。
- 被砍条目**不再出现**在后续刊物(现状即如此,符合"不顺延")。
- 用户要能**及时决策**:21:00 UTC 提醒里预告超额与将被砍的条目,用户在原卡片上改 drop 自行调换;
  到 finalize 仍未处理 → 按规则自动砍、照常出刊,砍掉的列在 finalize 后的 TG 汇总里。
  **这部分改的是 TG 消息,不在 A1,作为 A1 之后的独立小 PR(A1b)。**
- 决策拉取失败:同现状,按零决策出空稿并记 `decisions_fetch_error`;顺手改正 `tick.py:226` 过时注释("未审默认 keep")。
- 快照表读取异常:不崩,全部按 `no_snapshot` 跳过。
- 空稿静默不推 TG(同现状)。

## 验收标准(TDD,先写失败测试)

1. collect 后每张已推卡片在 `review_snapshots` 有对应行。
2. finalize 的 LLM 解读调用次数 = keep 且有快照的条数(假 LLM 计数)。
3. 无快照 / 解读失败 / 不相关 / 模型不一致 / 配额砍掉的条目不进报告,且各有正确 reason 的跳过日志。
4. finalize 不调用 collect(桩函数被调用即抛错)。
5. 走 `run_tick` 真实入口的接线测试(快照 + 假决策 → 报告条目正确)。
6. 真实验证(`--tick` 不认 `--dry-run`,审核 #4,本次不修):scratchpad 脚本用临时库跑一次真实 collect →
   挑 2 条 keep 的假决策 → 真实 finalize(真 LLM,决策源/网站/TG 换成 fake,不写仓库不发消息)→ 产出 2 条内容,
   且 `interpret_done` 显示 LLM 真的成功(不是全回退),作为 PR 证据。

## 不在 A1 内

- E3 记账集合(`mark_published` 用配额前 `report_items`)与 E2 并发保护 → B。
- 保留条目间同事件合并 → A2(`storylink.py` 本次只是不再调用,不删)。
- 已知风险:快照仍在 Actions cache,缓存丢失 = 当晚全部 `no_snapshot` = 空稿,由 B 解决。
