# M2-B2 设计 — 配额提量 + 信号闸压噪声

- 日期:2026-06-22
- 来源:M2-B brainstorm Q1=C(信号闸 + 地板),用户加注:商用模型/大厂产品更新 权重+数量拉高
- 状态:待用户复审 → 实现

## 目标
1. 日报从 8 条提到 ~10-11,尤其给**商用模型(model)+ 大厂产品更新(announcement)** 更多坑 + 更高权重。
2. 压 firehose 噪声(个人随手传、零人气的 model/writeup,如 gemma-...-GGUF 上榜)。
3. 宁缺毋滥:不够好的不凑数。

## 改动(全 config + score.py 一处)

### 1. `config/scoring.yaml`
- `total_limit: 8 → 11`。
- `quota`(per-genre **上限**,和可 > total_limit,最终由 total_limit + 60 地板截):
  `{paper: 3, model: 3, announcement: 3, writeup: 2, news: 1}`。
- `genre_value` 拉高商用/大厂(产业影响 + 扩散潜力):
  - `announcement: {一手性:20, 技术价值:10, 产业影响:16, 扩散潜力:12}`(原 12/9)。
  - `model: {一手性:18, 技术价值:14, 产业影响:12, 扩散潜力:9}`(产业影响 10→12)。
- 新增 `firehose_penalty: -20`。

> 商用模型/大厂本就靠 `publisher_authority`(company 14/lab 18 vs individual 8)领先;再叠 genre_value 拉高 + 信号闸把个人噪声压下去,二者拉开差距。

### 2. `src/core/types.py`
`ScoringConfig` 加 `firehose_penalty: float = -20.0`。

### 3. `src/pipeline/score.py`
`compute_scores` 里给 breakdown 的"惩罚"维度叠加信号闸:
```python
firehose = (
    config.firehose_penalty
    if it.genre.value in ("model", "writeup")
    and it.publisher.value == "individual"
    and _popularity_proxy(it, config) == 0
    else 0.0
)
```
加进 `"惩罚": penalty_of[it.link] + firehose`(与同源惩罚同维度叠加)。

### 4. 质量地板
**复用** M2-A 已有的 publish `min_display_score: 60`。**不加**每类独立地板(flat 60 + 信号闸已够;某 genre 系统性误判再加)。

## 验收 / 测试(纯函数,golden 易测)
- 信号闸:个人 + model/writeup + 零人气 → 扣 -20;有人气(likes>0)或 company/lab 不扣;paper/news 不受影响。
- gemma 类(71)→ 51 < 60 → 不进正刊;商用模型(company,有 likes)不误伤。
- 配额:total_limit 11;announcement/model 能占到 3 坑(若有货)。
- 全量 `uv run pytest` 绿 + lint。

## 不在本 spec
GitHub 源 / `tool` genre(子项目2)。每类独立地板(按需再加)。
