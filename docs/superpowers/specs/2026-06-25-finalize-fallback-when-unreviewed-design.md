# 零决策兜底自动出报 — 设计

日期: 2026-06-25 · 触发: 2026-06-24 日报零条(用户未在 TG 点确认)

## 问题

`#39`(commit dd0030b)上线确认门: finalize 只发显式 keep/edit 的条目
([tick.py](../../../src/pipeline/tick.py) `select_report_items`)。
失败模式: 用户某天**完全没碰 TG**(零决策)→ `select_report_items` 返回 `[]`
→ 当日发出一份**空报告**。2026-06-24 即此情形。

确认门的意图(不误发未审内容)是对的; 但"人没空 = 零产出"太脆。需要兜底。

## 目标 / 验收标准

1. 整个 run **零决策**(没碰 TG)→ 自动发"打分自动选的当日 top-N", 不再空报。
2. 用户**有参与**(≥1 条 keep/drop)→ 确认门不变: 只发 keep/edit; 全 drop / 只看不留 → 仍空(尊重显式选择)。
3. 兜底报标记为**草稿**(`draft:true`)+ "草稿待定稿"水印, 事后可 flip 成正式。
4. 改动外科手术式: 只动 finalize 的条目选择点, 不改确认门函数本身、不改 review/publish/webhook 协议。

## 设计

### 触发与行为

finalize 时 `decisions` 字典为空(本 run 无任何 keep/drop)→ 兜底:
把**全部 interpreted items** 交给下游 review→publish, 由 publish 既有的
`relevant` 过滤 + 地板(`min_display_score`) + per-genre 配额 + total_limit
自动截出合理 top-N(**不是发全部**)。

`decisions` 非空 → 维持现状: `select_report_items` 确认门, 只收 keep/edit。

### 标记(无需新增逻辑)

`is_pending = not is_reviewed`([review.py:147](../../../src/pipeline/review.py)), 而
`is_reviewed = 是否存在任何决策`。零决策时 `is_pending` 本就 True
→ 渲染层自动加 "草稿待定稿"水印 + front matter `draft:true`。**不改 pending 逻辑**。

### 改动位置

[tick.py](../../../src/pipeline/tick.py) `run_finalize_tick`, 把:

```python
report_items = select_report_items(interpreted_items, decisions)
```

改为:

```python
if decisions:
    report_items = select_report_items(interpreted_items, decisions)  # 确认门
else:
    # 零决策兜底: 自动发, 由 publish 的 relevant+地板+配额+total_limit 截 top-N
    report_items = list(interpreted_items)
```

`select_report_items` 本身不动(仍是 engaged 路径的门)。后续跨天去重
(`already_published_elsewhere`)、`review`、`publish` 全沿用。

### 数据流(改后)

```
finalize: fetch decisions
        ├─ decisions 非空 → select_report_items(keep/edit)   [确认门, 不变]
        └─ decisions 为空 → 全部 interpreted                  [零决策兜底, 新增]
        → 跨天去重 → review → publish(relevant+地板+配额+total_limit) → 发布
                                                  ↑ is_pending=True → draft+水印
```

## 一个已知取舍(写明)

`draft:true` 的报告在**网站**上可能不公开显示; 但 **Telegram final_report 消息照常带全文发出**
([tick.py:162](../../../src/pipeline/tick.py) 不看 draft 标志)。用户主要在 TG 读, 故兜底报内容可见,
网站侧保持草稿直到用户 flip。若日后要网站也显示兜底报, 再单独决策(本设计不做)。

## 不做(YAGNI)

- 不做 per-card 超时默认 keep(那等于撤销 #39)。
- 不做"零决策时发 TG 提醒并延迟出报"(用户要的是"忘了也有报", 不是催办)。
- 不改 webhook 决策协议 / DecisionStore。
- 不改 publish 的配额/地板(PR #49 已定)。

## 测试要点(TDD)

- 零决策(`decisions={}`)→ `report_items` 非空 = 全部 interpreted(交给 publish 截), 报告非空且 `is_pending=True`。
- 有 1 条 drop 决策 → 走确认门: 没 keep 则报告空(尊重)。
- 有 keep 决策 → 只发 keep/edit(确认门不变)。
- 兜底报渲染含 "草稿待定稿"水印 + `draft:true`。
