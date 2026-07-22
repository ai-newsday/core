# Design — GitHub release 重要性判定

- 日期:2026-07-22
- 状态:草案(待 spec review)
- 关联:#61(adapter_quota,只在最终发布生效,本设计在更早的 enrich 层拦截)、[[github-sources-releases-trending]] design(2026-06-23,adapter/信号地基)

## 问题

`github_releases` adapter 只存 `title/tag/link/published_at/signals.github_stars`。打分只用"源权威度(`priority`)+ 时效 + 人气(`github_stars`)"——star 是 repo 级常数,同一 repo 一天内的每个 release(不管是 patch bugfix 还是重大发布)拿到完全相同的人气分。结果:高 star 仓库的任意一条 patch release 都能挤进候选池(`card_pool_limit`),把真正的一手内容(模型发布、产品公告)从每日候选池里挤出去。

`#61` 加的 `adapter_quota: {github_releases: 2}` 只在 `publish.py`(人工审阅后的最终发布选择)生效,不作用于 `score.py` 的 `card_pool_limit` 候选池选取——候选池(即推 Telegram 给人审阅的那批卡片)依然被刷屏,这是本次要解决的根本问题。

## 目标 / 非目标

**目标**:在候选池选取之前(enrich 层),用 LLM 判断每条 release 的实质重要性,硬过滤掉纯维护性 patch,并让通过的 release 按重要程度参与排序。

**非目标(本次不做,写进"后续任务")**:
- **repo 清单更新与权重重排**——`config/sources.yaml` 里 30 个 `github_releases` 源的 `priority` 字段沿用现状,不在本次调整;"优先覆盖热门 AI/Agent 应用/算法库/工具库"的清单扩充与重排是独立的内容策展工作,不需要完整设计,后续单开小 PR。
- **跨 tag 内容去重**——验证时发现 cline 的 `cli-v3.0.44` 与 `sdk/sdk/v0.0.64` 内容完全重复(同一天两个 tag 各发一次),现状两者都会被本设计的硬过滤挡掉所以不影响;但若未来出现两条重复内容都被判为 tier≥2 的情况,`dedup.py` 现有单轮 link 去重抓不住这种跨 tag 重复,留给以后。

## 判定设计:4 个独立维度 + 纯函数映射

LLM 只做**布尔分类**,不做算术。四个维度各自独立判断,再用一个纯函数(可脱离 LLM 单元测试)映射到最终档位:

```python
def tier(scale: bool, refactor: bool, new_concept: bool, bugfix_only: bool) -> int:
    if refactor or new_concept:
        return 3 if scale else 2
    if scale and not bugfix_only:
        return 2
    return 1  # 常规补丁; 空 body 在调 LLM 之前已短路判 0
```

- **变动量大**(`scale`):改动量/PR 数是否显著,不看具体数字看叙述密度。
- **重构**(`refactor`):是否替换/重写现有子系统(而非新增)。
- **新概念**(`new_concept`):是否引入新模型家族/全新能力类目/首次发布的产品形态。
- **纯 bugfix/UI**(`bugfix_only`):是否只是数值修正/UI 文案微调,无新增能力面。

**空 body 短路**:body 去除 `**Full Changelog**: ...` 比较链接后剩余字符数 < 阈值(如 30)→ 直接判 tier 0,不调 LLM(省成本,也更确定)。

### Few-shot 校准(每维度正反例,来自 ComfyUI 真实 release)

| 维度 | 正例(命中) | 反例(未命中) |
|---|---|---|
| 变动量大 | `v0.21.0` — 40+ PR 打包,body 13959 字 | `v0.18.2` — body 只有一行比较链接,0 条变更 |
| 重构 | `v0.21.0` — "Use pyav to load images **instead of pillow**"(替换核心图像加载后端) | `v0.19.3` — 加个 SVG 模型节点、修价格标签,没动任何既有系统 |
| 新概念 | `v0.11.0` — "Support **zimage omni** base model"(全新模型家族接入) | `v0.16.1` — 更新已有定价、给已有节点加开关,现有能力上加参数 |
| 纯 bugfix/UI | `v0.18.1` — 4 条纯数值/渲染 bug 修复,零新增 | `v0.16.0` — "feat: Support SDPose-OOD" + "Native LongCat-Image implementation",明显新功能 |

四个例子 + 判定理由写进 `src/prompts/release_importance.md`(few-shot),运行时加载(CLAUDE.md「产品判断在 references/+src/prompts/」)。

### 跨 repo 验证(不只 ComfyUI)

拉了 cline / vllm / diffusers / litellm / open-webui / browser-use / unsloth / langchain / ruflo 的真实 release 校验:

- **直接命中今天投诉的 4 条 cline spam**(`sdk/sdk/v0.0.63`、`v0.0.64`、`cli-v3.0.43`、`cli-v3.0.44`):均为 1-2 条纯 bug fix,四维全 false → tier 1 → 会被硬过滤。同 repo 的 `desktop-v0.0.2`(桌面 App 首发)四维判 `scale=true, new_concept=true` → tier 3,正确保留,证明 rubric 不是无脑全砍。
- **截断预算问题**:`litellm` 的 release body 固定以 ~1050 字的 cosign 签名验证说明开头,真正的"What's Changed"内容被挤到后面。若照抄 `interpret.yaml` 的 `raw_summary_max_chars: 1500`,LLM 会看不到实际变更。→ **本设计的截断预算独立设为 3000 字**(仍远比全量便宜,分类任务不需要全文)。验证 `vllm`("752 commits from 320 contributors" highlight 直接给了 scale 信号)、`diffusers`、`open-webui` 即便 body 上万字,真正信号都在开头,3000 字内能看到。
- **`langchain` 的 tag 不是 `vX.Y.Z`**(是 `langchain-openrouter==0.2.7` 子包格式)——因为设计上不做机械 semver 解析、完全交给 LLM 整体读 body,不受 tag 格式影响,天然兼容。

## 架构:接入点

新建 `src/pipeline/release_importance.py`(不塞进 `enrich.py`——那个文件职责是"HN 反查 popularity 信号",这是另一件事:LLM 分类 + 硬过滤,混在一起违反单一职责)。

```python
def judge_release_importance(
    items: list[RawItem], llm: LLMProvider, config: ReleaseImportanceConfig, ctx: RunContext
) -> list[RawItem]:
    """只处理 adapter == "github_releases" 的条目; 其余原样透传。
    空 body 短路判 tier 0; 否则调 LLM 判 4 维 → tier() 映射。
    tier <= config.hard_filter_max_tier → 从返回列表剔除。
    tier >= 2 → 写 signals["release_tier_score"](映射见下), 参与打分。
    同步函数(非 async)——OpenAICompatLLM.complete_json 本身是阻塞的 httpx.Client 调用,
    跟随 interpret.py 的既有约定(顺序循环), 不是 enrich_with_hn 那种 async 客户端。
    """
```

`cli.py` 里紧跟 `enrich_with_hn` 之后调用(`collect → enrich_with_hn → judge_release_importance → dedup → score`),在候选池选取之前拦截,比 `#61` 的 `adapter_quota` 早两层生效。

## 打分接入:复用现有机制,不新增打分路径

`judge_release_importance` 给通过硬过滤的条目写 `signals["release_tier_score"]`(tier→分值映射,如 `{2: 4, 3: 9}`,config 可调)。这个 key 加进 `config/scoring.yaml` 的 `popularity_weights`,直接复用 `score.py` 现成的 `_visibility()` sqrt+cap 公式——**零新增打分代码**,只加一个 config key。`github_stars` 权重不变,repo 本身量级依然算数,只是不再是唯一信号。

## 容错(CLAUDE.md「宁可少写不可编造」在此处的解读)

LLM 调用失败 / JSON 解析失败 → **fail-open,默认 tier=2**(放行 + 中性打分,不硬删)。理由:这里的"宁可少写"原意是防止 AI 编造事实性内容;但对一个过滤器来说,LLM 服务抖动时把整批合法 release 误杀的代价,远大于让一条本该被过滤的 patch 多留一天。fail-open 让降级行为可预测(退化到"过滤关闭前"的状态),不是编造。

复用 `interpret.yaml` 的 provider/model 链配置格式(`config/enrich.yaml` 新增 `release_importance` 块,含 `providers`/`models`/`fallback_models`/`temperature`/`max_tokens`(小,布尔分类任务)/`timeout_s`/`prompt_path`),沿用现成的 `OpenAICompatLLM`,零新增 provider 代码。

## 配置(`config/enrich.yaml` 新增)

```yaml
release_importance:
  enabled: true
  providers: {...}          # 同 interpret.yaml 格式
  models: [...]
  fallback_models: [...]
  temperature: 0.1           # 分类任务, 低温度求稳定
  max_tokens: 300             # 只需 4 个布尔 + 一句 reason
  timeout_s: 30              # 单条 release 串行判定(见"架构:接入点"), 无 concurrency 字段
  prompt_path: "src/prompts/release_importance.md"
  empty_body_min_chars: 30    # 短于此判 tier 0, 不调 LLM
  hard_filter_max_tier: 1     # tier <= 此值从候选池剔除
  tier_score: {2: 4, 3: 9}    # tier -> release_tier_score(写入 popularity_weights)
```

`config/scoring.yaml` 的 `popularity_weights` 加一行:`release_tier_score: 1.0`(直接用映射后的分值,不需要额外加权;沿用 sqrt 压缩 + `popularity_cap: 15` 封顶)。

## 测试

- **纯函数测试**(无需 LLM/mock):`tier()` 映射函数的单元测试,覆盖所有 16 种布尔组合。
- **golden 测试**:喂 ComfyUI + cline 真实校准例子(fixture,含空 body/纯 bugfix/重构/新概念/大规模五种)作为 fake LLM 的固定输出,断言硬过滤生效 + `release_tier_score` 正确写入。
- **容错测试**:fake LLM 抛异常 → 断言条目保留(fail-open)、tier 视为 2、不阻断其余条目处理。
- **不改动** `github_releases` adapter 本身——它继续吐原始 `RawItem`,不掺判定逻辑,职责分离(adapter 测试不变)。

## 落地顺序(writing-plans 细化)

1. `config/types.py`:`EnrichConfig` 加 `release_importance` 子配置类型;`ScoringConfig.popularity_weights` 支持新 key(无需改类型,已是 `dict`)。
2. `src/prompts/release_importance.md`:写 prompt 模板 + 4 维 few-shot(ComfyUI 例子)。
3. `src/pipeline/release_importance.py`:`tier()` 纯函数 + `judge_release_importance()` 异步分类+过滤函数。
4. `config/enrich.yaml` / `config/scoring.yaml`:加新配置块。
5. `src/cli.py`:接入 `_collect_then_enrich` 流程,紧跟 `enrich_with_hn` 之后。
6. 测试(纯函数 + golden + 容错),对齐 CLAUDE.md「没有失败测试前不写实现代码」。
