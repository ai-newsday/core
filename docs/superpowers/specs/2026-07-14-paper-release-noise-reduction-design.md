# Paper + Releases 降噪 — 设计

日期: 2026-07-14 · 触发: KANBAN §3 P0 "主动降噪·Paper + GitHub Releases 重要性"; 实测 `content/posts/2026-07-09.md` / `2026-07-11.md`（master 真实发布产物）每篇 `github_releases` 条目都是原始英文 markdown、部分逐字截断到版本号中间。

## 根因（读真实发布产物 + 代码定位, 不猜）

三个独立问题, 同属"发布类噪声/低可读性":

**A. `github_releases` 的 `raw_summary` 不设上限, 撑爆 LLM prompt → 必然走 fallback。**
`src/adapters/sources/github_releases.py::fetch` 把 GitHub release body 整段（可能几 KB 的 changelog）原样塞进 `raw_summary`；`src/pipeline/interpret.py::build_item_prompt` 原样注入 prompt, 不截断。超长 prompt 让 LLM 调用失败/超时, `interpret_item` 捕获异常落到 `extractive_fallback`（`docs/specs/interpret.md` §5.3 明确的"零幻觉兜底", 机制本身没错）——但 `github_releases` 幼乎每次都命中这条路径, 用户看到的就是**未翻译、未截好句子**的原文残片。

**B. `_trim_to_sentence` 的句末标点集合把裸 `.` 也算句末**, 而技术文本（版本号 `v2.2.11-canary.4`、`e.g.`）里 `.` 大量出现在非句末位置。实测 2026-07-11 报告里 fallback 输出被切在 `` `v2.2.11-canary.` `` ——正是这个 bug。即使修了 A, fallback 仍会在少数情况触发（LLM 真挂时）, 这个 bug 还在。

**C. GitHub Releases API 已经返回 `prerelease: bool`，适配器读了 API 响应却丢弃它。** canary/rc/nightly 构建（`prerelease=true`）和正式发布拿一样的打分（评分层没有信号区分, `github_stars` 相同）, 同一仓库同一天可以出现 2 条 canary 占卡位。

**D. `hf-papers` 没有硬下限。** `SourceSpec.min_score` 字段已存在且 `hn.py` 已用它过滤低分 HN 条目；`hf_papers.py` 抓到 `upvotes` 信号但从不检查 `min_score`, 只靠 `scoring.yaml` 里 `popularity_weights.upvotes: 0.6` 的连续加权, 低质量论文仍能进候选池。`docs/recent-papers.md` 实测样本显示单日 upvotes 尾部低到 20 左右, 头部 60-185, 用作硬下限过滤"几乎无人关注"的论文足够, 不会误伤头部。

## 目标 / 验收

1. `github_releases` 条目命中 `extractive_fallback` 的比例下降（不定死具体值, 用 §7 metrics `fallback_breakdown` 按 genre 观察 —— 若需要 metrics 支持按 genre 拆分, 顺手加）。
2. 即使命中 fallback, 输出不再在版本号/缩写中间截断。
3. `prerelease=true` 的 release 不再出现在候选池（同仓库同日 canary 刷屏问题随之解决, 不需要额外的跨条目去重逻辑）。
4. `hf-papers` 低于阈值的论文不进候选池, 头部论文（近 5 天样本 top 2 均 ≥60）不受影响。
5. 三个改动各自独立可关（config 开关 / 阈值可调）, 互不耦合, 符合"一次一个模块"但按用户要求一个 PR 交付, 分 commit 验收。

## 设计

### §1 `InterpretConfig.raw_summary_max_chars` — 通用输入截断

`config/interpret.yaml` 新增字段（不写死代码里）:
```yaml
raw_summary_max_chars: 1500   # 防任意 adapter 的超长 raw_summary 撑爆 prompt; 留给 LLM 判重要性的余量
```

`src/core/types.py::InterpretConfig` 加字段:
```python
raw_summary_max_chars: int = 1500
```

`src/pipeline/interpret.py::build_item_prompt` 签名加 `config`, 用 `_trim_to_sentence` 截断后再替换占位符（复用 §2 修好的版本, 不新写一套截断逻辑）:
```python
def build_item_prompt(item: ScoredItem, template: str, config: InterpretConfig) -> str:
    raw_summary = _trim_to_sentence(item.raw_summary or "", config.raw_summary_max_chars)
    repl = {..., "{{raw_summary}}": raw_summary}
    ...
```
调用方 `interpret_item` 传入 `config`（本来就有）。**这是通用截断**, 对所有 adapter 生效, 不特判 `github_releases`——DRY, 也顺带保护未来任何返回超长 raw_summary 的新源。

### §2 `_trim_to_sentence` 句末判定修 bug

`src/pipeline/interpret.py`:
```python
_SENT_ENDS = "。！？!?；;"   # 去掉裸 "."

def _trim_to_sentence(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    window = text[:n]
    # "." 只在其后紧跟空白或就是窗口末尾时才算句末, 避开版本号/缩写
    dot_cut = -1
    for i, ch in enumerate(window):
        if ch == "." and (i + 1 == len(window) or window[i + 1].isspace()):
            dot_cut = i
    cut = max([window.rfind(ch) for ch in _SENT_ENDS] + [dot_cut], default=-1)
    if cut >= 0:
        return window[: cut + 1]
    return text[: n - 1] + "…"
```
只改 `.` 的判定方式, 中英文标点集合不变, 现有 golden/contract 里非 `.` 结尾的用例不受影响。

### §3 `github_releases` 过滤 `prerelease`

`src/adapters/sources/github_releases.py::fetch`:
```python
for r in releases:
    if r.get("prerelease"):
        continue
    ...
```
放在现有 `published`/`tag`/`html_url` 校验旁边, 同一层过滤逻辑, 不新增分支结构。**不做**额外的"同仓库同日去重"——`prerelease` 过滤已经覆盖实测场景（两条 canary 都是 `prerelease=true`）, 若未来出现同日两条正式 release 的边界情况, 留给评分层/审阅层的人工 keep/drop 处理, 不预先造轮子。

### §4 `hf_papers.py` 加 `min_score` 门槛

`src/adapters/sources/hf_papers.py::fetch`, 照抄 `hn.py` 现成模式:
```python
async def fetch(self, source: SourceSpec, ctx: RunContext, timeout_s: int) -> list[RawItem]:
    ...
    for row in data:
        paper = row.get("paper", {})
        pid, title = paper.get("id"), paper.get("title")
        upvotes = paper.get("upvotes")
        if source.min_score is not None and (upvotes or 0) < source.min_score:
            continue
        ...
```

`config/sources.yaml` 给 `hf-papers` 条目加阈值（现状无 `min_score` 字段 = 不过滤）:
```yaml
- {name: hf-papers, url: "...", genre: paper, publisher: company, adapter: hf_papers, status: working, priority: 1, min_score: 15}
```
`min_score: 15` 依据 `docs/recent-papers.md` 5 天样本尾部分布定, 留 config 注释写明依据和调整方式, 不锁死。

## 替代方案（拒）

| 方案 | 拒因 |
|---|---|
| 在 `github_releases.py` adapter 里单独截断 `raw_summary`（不动 interpret.py） | 只治 releases 一个源, 下次新源（如 changelog 类）超长又得重写一遍; §1 放 interpret.py 通用截断更 DRY |
| 完全跳过 `prerelease=true` 的 fallback, 直接不 interpret（更早的阶段过滤） | 就是 §3 的做法本身, 已采纳; 唯一区别是过滤点选在 adapter fetch 而非 collect/score 层——**adapter 层过滤最省事**, 因为 `RawItem` 根本不会生成, 不占后续任何阶段的计算 |
| 加"同仓库同日去重"规则（跨条目逻辑） | `prerelease` 过滤已解决实测的具体案例; 无证据证明还有别的重复模式, YAGNI, 等以后真遇到再加 |
| `hf-papers` 用相对排名过滤（"每日 top N 才留"）而非绝对 `min_score` | quota 层（`score.py::apply_quota`）已经做 top-N 截断; adapter 层的 `min_score` 是**候选池噪声地板**, 二者不冲突, 相对排名放 quota 更合适, 不重复造 |
| `min_score` 阈值定更高（如 30）"更保险" | 会误伤头部尾巴的正常论文（样本里 top-2 之外仍有 20-40 分的合理候选）, 15 是尾部噪声和正常论文的粗略分界, 先上线用 metrics 观察再调 |

## 实施顺序（3 commit, 单 PR, 用户已确认一次性做完）

| # | 内容 | 验证 |
|---|---|---|
| **C1** | `_trim_to_sentence` 句末判定修 bug（§2）+ 单元测试（版本号/缩写场景不再中间截断, 中英文标点场景不回归） | pytest 全绿 |
| **C2** | `InterpretConfig.raw_summary_max_chars` + `build_item_prompt` 截断（§1）+ 单元测试（超长 raw_summary 被截, 短的不变） | pytest 全绿 |
| **C3** | `github_releases.py` 过滤 `prerelease`（§3）+ `hf_papers.py` 加 `min_score`（§4）+ `config/sources.yaml` 设 `min_score: 15` + 两个 adapter 的 contract test | pytest 全绿 + dry-run smoke 对比过滤前后候选数 |

## 测试矩阵

| 层 | 测试 | 类型 |
|---|---|---|
| `_trim_to_sentence` | `"...v2.2.11-canary.4 更多文字"` 超长截断不落在版本号中间 | 单元 |
| `_trim_to_sentence` | 中文句末标点场景（现有用例）不回归 | 单元（回归） |
| `build_item_prompt` | `raw_summary` 超 `raw_summary_max_chars` → 被截; 短的原样保留 | 单元 |
| `GithubReleasesAdapter.fetch` | mock 响应含 `prerelease=true` 条目 → 不出现在结果里；`prerelease=false` 正常收录 | contract |
| `HFPapersAdapter.fetch` | mock 响应含 `upvotes` 低于 `source.min_score` → 过滤；`min_score=None` → 不过滤（向后兼容） | contract |
| e2e | `--dry-run` 对比改动前后同一份历史 payload 的候选数 + fallback 条数变化 | integration/手验 |

## 不做（YAGNI）

- 同仓库同日跨条目去重（§替代方案已述, 等实测出现真实案例再加）
- 按 `major.minor.patch` 差值判定"是否重要更新"（KANBAN 原始描述提过, 但 `prerelease` 已覆盖实测噪声源头, 版本号差值判定复杂度高且脆, 先不做）
- release note 关键词打分（"breaking/new model/benchmark"）——同上, 证据不足以证明 `prerelease` 过滤后仍有明显噪声剩余
- `hf-papers` 话题相关性打分（KANBAN 提过的备选）——`min_score` 更简单且复用现成模式, 先上线看效果, 不够再加
- 按 genre 拆分 `fallback_breakdown` metrics（§目标验收提到"顺手加"但非本 spec 核心, 若 C1-C3 验证不够用再补）

## 关联

- KANBAN §3 P0 "主动降噪·Paper + GitHub Releases 重要性" — 本 spec（完成后打钩, 并顺手把 KANBAN 里已完成但仍标 ☐ 的"放宽发卡池"/"翻译失效根治"/"metrics dashboard"/"Reddit PRAW"（已被 #55 的 .rss 方案取代）一并勾掉, 修正 KANBAN 落后于 master 实际进度的问题）
- `docs/specs/interpret.md` §5.3 — fallback 机制本身的契约, 本 spec 不改契约, 只堵住"几乎必然触发 fallback"和"触发后仍然难看"两个洞
- Memory `[[paper-source-preference]]` — hf-papers upvotes 是唯一信任信号, 本 spec 的 `min_score` 直接用这个信号, 不引入新信号源
- `docs/competitive-analysis-ai-news.md` §12b（本次新增）— alphasignal.ai 的"零截断单句标题"风格作为本 spec 目标状态的风格参照
