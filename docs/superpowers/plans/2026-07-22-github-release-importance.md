# GitHub Release 重要性判定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 enrich 层用 LLM 对 `github_releases` 条目做 4 维布尔分类(scale/refactor/new_concept/bugfix_only),用纯函数映射成 0-3 档,硬过滤纯维护性 patch(候选池选取之前),让通过的条目按重要程度参与打分。

**Architecture:** 新文件 `src/pipeline/release_importance.py` 导出 `tier()`(纯函数)和 `judge_release_importance()`(同步——跟随 `interpret.py` 的既有约定,`OpenAICompatLLM.complete_json` 本身是同步 `httpx.Client`,不是 async;`enrich_with_hn` 是 async 只因为它注入的 HN 客户端是 async,这里不适用)。`cli.py` 在 `enrich_with_hn` 之后紧接调用,写 `signals["release_tier_score"]` 复用现成的 `popularity_weights` 打分机制。

**Tech Stack:** Python 3.12, dataclass config, pytest, 复用 `tests/fakes.py` 现成的 `FakeLLMProvider`/`FailingLLMProvider`。

## Global Constraints

- TDD:每个实现步骤前必须先写失败测试(CLAUDE.md「没有失败测试前不写实现代码」)。
- 打分/过滤逻辑做成纯函数(`tier()`),网络/LLM 隔离在 `judge_release_importance()`。
- 阈值全部读 `config/enrich.yaml`,不写死。
- LLM 一律结构化 JSON;解析失败/调用失败 → fail-open 视为 tier=2(放行 + 中性打分),不硬删、不编造。
- 产品判断(4 维 few-shot 例子)放 `src/prompts/`,运行时加载。
- 只碰该碰的:不改 `github_releases` adapter 本身、不改 `dedup.py`/`score.py` 的既有函数体(只加 config key)。

---

## Task 1: `ReleaseImportanceConfig` 类型 + `EnrichConfig` 嵌套 + loader

**Files:**
- Modify: `src/core/types.py`(在 `EnrichConfig` 定义之前,约第 400 行,`ProviderSpec`/`_DEFAULT_MODELSCOPE` 已在第 222-230 行定义)
- Modify: `src/core/config.py`(`load_enrich_config`,约第 168 行)
- Test: `tests/contract/test_enrich_config.py`

**Interfaces:**
- Produces: `ReleaseImportanceConfig` dataclass(字段:`enabled: bool`, `model: str`, `models: list[str]`, `fallback_models: list[str]`, `providers: dict[str, ProviderSpec]`, `temperature: float`, `max_tokens: int`, `timeout_s: int`, `empty_body_min_chars: int`, `hard_filter_max_tier: int`, `tier_score: dict[int, float]`, `prompt_path: str`)
- Produces: `EnrichConfig.release_importance: ReleaseImportanceConfig` 字段
- Produces: `load_enrich_config(path) -> EnrichConfig`(已存在,本任务扩展其解析新块)

- [ ] **Step 1: 写失败测试(config 默认值 + yaml 覆盖 + providers 解析)**

在 `tests/contract/test_enrich_config.py` 末尾追加:

```python
from src.core.types import ProviderSpec, ReleaseImportanceConfig


def test_enrich_config_release_importance_defaults():
    cfg = EnrichConfig()
    ri = cfg.release_importance
    assert isinstance(ri, ReleaseImportanceConfig)
    assert ri.enabled is True
    assert ri.hard_filter_max_tier == 1
    assert ri.tier_score == {2: 4.0, 3: 9.0}
    assert ri.empty_body_min_chars == 30
    assert ri.prompt_path == "src/prompts/release_importance.md"
    assert "modelscope" in ri.providers


def test_load_enrich_config_release_importance_overrides(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text(
        """
release_importance:
  enabled: false
  models: ["modelscope:deepseek-ai/DeepSeek-V4-Flash"]
  fallback_models: ["agnes:agnes-2.0-flash"]
  temperature: 0.1
  max_tokens: 300
  timeout_s: 20
  empty_body_min_chars: 40
  hard_filter_max_tier: 2
  tier_score: {2: 5, 3: 10}
  prompt_path: "src/prompts/release_importance.md"
  providers:
    modelscope:
      base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
      api_key_env: "MODELSCOPE_API_KEY"
    agnes:
      base_url: "https://apihub.agnes-ai.com/v1/chat/completions"
      api_key_env: "AGNES_API_KEY"
""",
        encoding="utf-8",
    )
    cfg = load_enrich_config(str(p))
    ri = cfg.release_importance
    assert ri.enabled is False
    assert ri.models == ["modelscope:deepseek-ai/DeepSeek-V4-Flash"]
    assert ri.fallback_models == ["agnes:agnes-2.0-flash"]
    assert ri.timeout_s == 20
    assert ri.empty_body_min_chars == 40
    assert ri.hard_filter_max_tier == 2
    assert ri.tier_score == {2: 5, 3: 10}
    assert set(ri.providers.keys()) == {"modelscope", "agnes"}
    assert isinstance(ri.providers["agnes"], ProviderSpec)
    assert ri.providers["agnes"].api_key_env == "AGNES_API_KEY"


def test_load_enrich_config_release_importance_missing_block_uses_defaults(tmp_path):
    p = tmp_path / "enrich.yaml"
    p.write_text("enabled: true\n", encoding="utf-8")
    cfg = load_enrich_config(str(p))
    assert cfg.release_importance == ReleaseImportanceConfig()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_enrich_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReleaseImportanceConfig'` 或 `AttributeError: 'EnrichConfig' object has no attribute 'release_importance'`

- [ ] **Step 3: 实现 `ReleaseImportanceConfig` + 接入 `EnrichConfig`**

在 `src/core/types.py` 里,`_DEFAULT_MODELSCOPE`(第 222-230 行附近)之后、`EnrichConfig`(现第 406 行)定义之前插入:

```python
@dataclass
class ReleaseImportanceConfig:
    """LLM 判定 github_releases 条目的实质重要性(spec 2026-07-22)。
    4 个独立布尔维度(scale/refactor/new_concept/bugfix_only) -> tier() 纯函数映射。"""

    enabled: bool = True
    model: str = "modelscope:deepseek-ai/DeepSeek-V4-Flash"
    models: list[str] = field(default_factory=list)
    fallback_models: list[str] = field(default_factory=list)
    providers: dict[str, ProviderSpec] = field(
        default_factory=lambda: {"modelscope": _DEFAULT_MODELSCOPE}
    )
    temperature: float = 0.1
    max_tokens: int = 300
    timeout_s: int = 30
    empty_body_min_chars: int = 30  # 去掉 Full Changelog 链接后正文短于此 -> 短路判 tier 0, 不调 LLM
    hard_filter_max_tier: int = 1  # tier <= 此值从候选池剔除
    tier_score: dict[int, float] = field(default_factory=lambda: {2: 4.0, 3: 9.0})
    prompt_path: str = "src/prompts/release_importance.md"
```

然后修改 `EnrichConfig`(现有内容不删,只加一个字段):

```python
@dataclass
class EnrichConfig:
    """RSS 类源天然无 popularity, 用 HN Algolia by URL 反查补 signals.hn_*。"""

    enabled: bool = True
    concurrency: int = 5
    timeout_s: int = 8
    # 已经带原生 popularity 信号的 genre 不查 HN (省请求, 不覆盖)
    skip_genres: list[str] = field(default_factory=lambda: ["paper", "model"])
    release_importance: ReleaseImportanceConfig = field(default_factory=ReleaseImportanceConfig)
```

- [ ] **Step 4: 扩展 `load_enrich_config` 解析新块**

在 `src/core/config.py`,把现有 `load_enrich_config` 函数整体替换为:

```python
def load_enrich_config(path: str) -> EnrichConfig:
    """HN URL 反查 popularity 的开关 + 配额, 以及 release 重要性判定; 缺文件 -> 默认。"""
    from src.core.types import ProviderSpec, ReleaseImportanceConfig  # local import to avoid cycles

    data = _read_yaml(path)
    d = EnrichConfig()
    ri_data = data.get("release_importance", {})
    ri_d = ReleaseImportanceConfig()
    raw_providers = ri_data.get("providers")
    if raw_providers:
        ri_providers = {
            name: ProviderSpec(base_url=spec["base_url"], api_key_env=spec["api_key_env"])
            for name, spec in raw_providers.items()
        }
    else:
        ri_providers = ri_d.providers
    release_importance = ReleaseImportanceConfig(
        enabled=ri_data.get("enabled", ri_d.enabled),
        model=ri_data.get("model", ri_d.model),
        models=ri_data.get("models", ri_d.models),
        fallback_models=ri_data.get("fallback_models", ri_d.fallback_models),
        providers=ri_providers,
        temperature=ri_data.get("temperature", ri_d.temperature),
        max_tokens=ri_data.get("max_tokens", ri_d.max_tokens),
        timeout_s=ri_data.get("timeout_s", ri_d.timeout_s),
        empty_body_min_chars=ri_data.get("empty_body_min_chars", ri_d.empty_body_min_chars),
        hard_filter_max_tier=ri_data.get("hard_filter_max_tier", ri_d.hard_filter_max_tier),
        tier_score=ri_data.get("tier_score", ri_d.tier_score),
        prompt_path=ri_data.get("prompt_path", ri_d.prompt_path),
    )
    return EnrichConfig(
        enabled=data.get("enabled", d.enabled),
        concurrency=data.get("concurrency", d.concurrency),
        timeout_s=data.get("timeout_s", d.timeout_s),
        skip_genres=data.get("skip_genres", d.skip_genres),
        release_importance=release_importance,
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_enrich_config.py -v`
Expected: PASS(全部,包括原有 4 个 + 新增 3 个)

- [ ] **Step 6: 跑全量 contract 测试确认没有连带破坏**

Run: `uv run pytest tests/contract/ -v -k enrich`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/core/types.py src/core/config.py tests/contract/test_enrich_config.py
git commit -m "feat(enrich): add ReleaseImportanceConfig for release-importance judgment"
```

---

## Task 2: Prompt 模板(4 维 few-shot)

**Files:**
- Create: `src/prompts/release_importance.md`
- Test: `tests/contract/test_prompts.py`

**Interfaces:**
- Consumes: 无(纯内容文件)
- Produces: `load_prompt("src/prompts/release_importance.md")` 返回含 `{{title}}`/`{{body}}` 占位符、且指导 LLM 输出 `{"scale":...,"refactor":...,"new_concept":...,"bugfix_only":...,"reason":...}` 的模板字符串,供 Task 4 的 `build_prompt()` 使用

- [ ] **Step 1: 写失败测试**

在 `tests/contract/test_prompts.py` 末尾追加:

```python
def test_release_importance_prompt_exists_and_has_placeholders():
    t = load_prompt("src/prompts/release_importance.md")
    assert "{{title}}" in t and "{{body}}" in t


def test_release_importance_prompt_has_four_dimension_schema():
    t = load_prompt("src/prompts/release_importance.md")
    for key in ('"scale"', '"refactor"', '"new_concept"', '"bugfix_only"', '"reason"'):
        assert key in t
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_prompts.py -v -k release_importance`
Expected: FAIL — `FileNotFoundError: [Errno 2] No such file or directory: 'src/prompts/release_importance.md'`

- [ ] **Step 3: 写 prompt 文件**

创建 `src/prompts/release_importance.md`:

```markdown
你是 GitHub AI 项目 release 的重要性评审员。基于给定的 release 信息,判断它在 4 个独立维度上是否成立。

硬约束(必须遵守):
- 只依据下方提供的 release 信息判断,不得编造未提及的内容。
- 4 个维度相互独立,每个单独给布尔值,不要互相绑定判断。

维度定义(每个维度都给一正一反的真实 ComfyUI release 例子参考):

1. `scale`(变动量是否显著):改动量/涉及的 PR 数是否明显偏多,看叙述密度不看具体数字。
   - 命中例:`v0.21.0` — 40+ 条 PR 打包在一次 release 里,涉及图像加载、VRAM、视频模型等多个子系统。
   - 未命中例:`v0.18.2` — 只有一行 "Full Changelog" 比较链接,没有任何变更条目。

2. `refactor`(是否重构):是否替换/重写了现有子系统(而非单纯新增)。
   - 命中例:`v0.21.0` — "Use pyav to load images **instead of pillow**"(替换核心图像加载后端)。
   - 未命中例:`v0.19.3` — 加个 SVG 模型节点支持、修个价格标签显示,没有替换任何既有系统。

3. `new_concept`(是否引入新概念):是否首次接入全新模型家族、全新能力类目,或首次发布的产品形态。
   - 命中例:`v0.11.0` — "Support **zimage omni** base model"(全新模型家族接入)。
   - 未命中例:`v0.16.1` — 更新已有第三方模型定价、给已有节点加个开关,都是在现有能力上加参数。

4. `bugfix_only`(是否纯 bugfix/UI 微调):是否只是数值修正、崩溃修复、UI 文案微调,没有任何新增能力面。
   - 命中例:`v0.18.1` — 4 条纯数值精度/渲染 bug 修复,零新增。
   - 未命中例:`v0.16.0` — "feat: Support SDPose-OOD" + "Native LongCat-Image implementation",明显是新功能而非修 bug。

只输出 JSON,结构如下(不要额外解释):
{"scale": false, "refactor": false, "new_concept": false, "bugfix_only": true, "reason": "一句话说明依据"}

Release 信息:
- 标识: {{title}}
- Changelog 正文: {{body}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_prompts.py -v -k release_importance`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/prompts/release_importance.md tests/contract/test_prompts.py
git commit -m "feat(prompts): add release_importance 4-dimension few-shot template"
```

---

## Task 3: `tier()` 纯函数

**Files:**
- Create: `src/pipeline/release_importance.py`
- Test: `tests/contract/test_release_importance_unit.py`

**Interfaces:**
- Produces: `tier(scale: bool, refactor: bool, new_concept: bool, bugfix_only: bool) -> int`(纯函数,返回 1-3;tier 0 由调用方在此函数之外的空 body 短路判定,不经过这个函数)

- [ ] **Step 1: 写失败测试(全部 16 种布尔组合)**

创建 `tests/contract/test_release_importance_unit.py`:

```python
import pytest

from src.pipeline.release_importance import tier

# (scale, refactor, new_concept, bugfix_only) -> expected tier
# 穷举全部 16 种组合, 由 tier() 的判定逻辑手算得出 (spec §判定设计)
CASES = [
    ((False, False, False, False), 1),
    ((False, False, False, True), 1),
    ((False, False, True, False), 2),
    ((False, False, True, True), 2),
    ((False, True, False, False), 2),
    ((False, True, False, True), 2),
    ((False, True, True, False), 2),
    ((False, True, True, True), 2),
    ((True, False, False, False), 2),
    ((True, False, False, True), 1),
    ((True, False, True, False), 3),
    ((True, False, True, True), 3),
    ((True, True, False, False), 3),
    ((True, True, False, True), 3),
    ((True, True, True, False), 3),
    ((True, True, True, True), 3),
]


@pytest.mark.parametrize("dims,expected", CASES)
def test_tier_all_16_combinations(dims, expected):
    scale, refactor, new_concept, bugfix_only = dims
    assert tier(scale, refactor, new_concept, bugfix_only) == expected


def test_tier_refactor_or_new_concept_dominates_scale():
    # refactor/new_concept 命中时, scale 决定是 2 还是 3, bugfix_only 被忽略
    assert tier(scale=False, refactor=True, new_concept=False, bugfix_only=True) == 2
    assert tier(scale=True, refactor=True, new_concept=False, bugfix_only=True) == 3


def test_tier_never_returns_zero():
    # tier 0 (空 body) 是调用方短路判定, 不经过 tier(), 所以 tier() 本身最低返回 1
    for dims, _ in CASES:
        assert tier(*dims) >= 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_release_importance_unit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.pipeline.release_importance'`

- [ ] **Step 3: 实现 `tier()`**

创建 `src/pipeline/release_importance.py`:

```python
"""release_importance: LLM 判定 github_releases 条目的实质重要性 (spec 2026-07-22)。
只处理 adapter == "github_releases" 的条目; 其余原样透传。空 body 短路判 tier 0
(不调 LLM); 否则 LLM 判 4 个独立布尔维度, tier() 纯函数映射到最终档位。
tier <= hard_filter_max_tier 的条目从返回列表剔除; tier >= 2 的条目写
signals["release_tier_score"] 参与打分 (复用 popularity_weights 机制)。
LLM 调用失败/解析失败 -> fail-open, 视为 tier=2 (放行 + 中性打分), 不硬删。"""

from __future__ import annotations


def tier(scale: bool, refactor: bool, new_concept: bool, bugfix_only: bool) -> int:
    """4 个独立布尔维度 -> 最终档位 (纯函数)。
    refactor/new_concept 命中 -> 3 (有规模) 或 2 (无规模);
    否则 scale 且非纯 bugfix -> 2; 其余(含空组合、纯 bugfix) -> 1。"""
    if refactor or new_concept:
        return 3 if scale else 2
    if scale and not bugfix_only:
        return 2
    return 1
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_release_importance_unit.py -v`
Expected: PASS(19 个测试:16 组合 + 2 个补充 + 1 个 never-zero)

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/release_importance.py tests/contract/test_release_importance_unit.py
git commit -m "feat(release-importance): add tier() pure mapping function"
```

---

## Task 4: `judge_release_importance()` + golden 测试

**Files:**
- Modify: `src/pipeline/release_importance.py`(在 Task 3 基础上追加)
- Test: `tests/golden/test_release_importance.py`

**Interfaces:**
- Consumes: `tier()` from Task 3;`RawItem`/`RunContext` from `src.core.types`;`load_prompt` from `src.core.prompts`;`emit` from `src.observability.events`;`FakeLLMProvider`/`FailingLLMProvider` from `tests.fakes`(测试用)
- Produces: `judge_release_importance(items: list[RawItem], llm, config: ReleaseImportanceConfig, ctx: RunContext) -> list[RawItem]` — Task 6(cli.py 接入)直接调用此函数

- [ ] **Step 1: 写失败测试**

创建 `tests/golden/test_release_importance.py`:

```python
"""golden: judge_release_importance 用伪 LLM, 验证硬过滤 + 打分信号注入 + 容错。"""

import json
import logging
from datetime import datetime, timezone

from src.core.types import Genre, Publisher, RawItem, ReleaseImportanceConfig, RunContext
from src.pipeline.release_importance import judge_release_importance
from tests.fakes import FailingLLMProvider, FakeLLMProvider

NOW = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)


def _release_item(title_en, raw_summary, link=None, adapter="github_releases"):
    return RawItem(
        title_en=title_en,
        link=link or f"https://github.com/x/y/releases/tag/{title_en}",
        source="x-gh",
        genre=Genre.announcement,
        publisher=Publisher.company,
        published_at=NOW,
        raw_summary=raw_summary,
        adapter=adapter,
    )


def _ctx():
    return RunContext(run_id="g", now=NOW, logger=logging.getLogger("golden-release-importance"))


def _dims_json(scale, refactor, new_concept, bugfix_only, reason="test"):
    return json.dumps(
        {
            "scale": scale,
            "refactor": refactor,
            "new_concept": new_concept,
            "bugfix_only": bugfix_only,
            "reason": reason,
        }
    )


def test_non_release_adapter_passthrough_no_llm_call():
    items = [_release_item("v1", "some body " * 10, adapter="rss")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert out == items
    assert llm.calls == []


def test_empty_body_short_circuits_to_tier0_filtered_no_llm_call():
    items = [_release_item("v0.18.2", "**Full Changelog**: https://github.com/x/y/compare/a...b")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert out == []  # tier 0 <= hard_filter_max_tier(1) -> 剔除
    assert llm.calls == []  # 短路, 不调 LLM


def test_bugfix_only_filtered_out():
    # raw_summary 故意写够 30+ 字, 确保走到 LLM 判定路径而不是空 body 短路
    # (空 body 短路本身由另一个用例单独测, 这里要测的是 LLM 判 bugfix_only=True 之后的硬过滤)
    items = [_release_item("v3.0.44", "Fixed a crash that occurred when loading malformed config files.")]
    llm = FakeLLMProvider({"v3.0.44": _dims_json(False, False, False, True)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认真的走了 LLM 判定, 不是空 body 短路蒙对
    assert out == []  # tier 1 <= hard_filter_max_tier(1) -> 剔除


def test_new_concept_with_scale_kept_with_tier3_score():
    items = [_release_item("v0.11.0", "Support zimage omni base model, huge refactor batch")]
    llm = FakeLLMProvider({"v0.11.0": _dims_json(True, False, True, False)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert len(out) == 1
    assert out[0].signals["release_tier_score"] == 9.0  # tier 3 -> tier_score 默认 {2:4.0,3:9.0}


def test_refactor_without_scale_kept_with_tier2_score():
    items = [_release_item("v0.21.0-mini", "A small but real refactor of one internal module, no scale.")]
    llm = FakeLLMProvider({"v0.21.0-mini": _dims_json(False, True, False, False)})
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认走了 LLM 判定
    assert len(out) == 1
    assert out[0].signals["release_tier_score"] == 4.0  # tier 2

def test_llm_failure_fails_open_to_tier2_kept():
    items = [_release_item("v9.9.9", "Some real changelog content here, long enough to skip short-circuit.")]
    llm = FailingLLMProvider()
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert llm.calls != []  # 确认真的调用了 LLM(才失败), 不是被短路跳过
    assert len(out) == 1  # fail-open: 不硬删
    assert out[0].signals["release_tier_score"] == 4.0  # 视为 tier 2, 中性打分


def test_disabled_passthrough_no_llm_call():
    items = [_release_item("v1", "bugfix only content")]
    llm = FakeLLMProvider({}, default=None)
    out = judge_release_importance(
        items, llm, ReleaseImportanceConfig(enabled=False), _ctx()
    )
    assert out == items
    assert llm.calls == []


def test_mixed_list_preserves_order_for_kept_items():
    items = [
        _release_item("a", "**Full Changelog**: https://x/compare/1...2"),  # tier0, 剔除
        _release_item("b", "big new concept work", adapter="rss"),  # 非 release, 透传(不检查长度)
        _release_item("c", "A large refactor batch touching several core subsystems at once."),  # tier3, 保留
    ]
    llm = FakeLLMProvider(
        {"c": _dims_json(True, True, False, False)}
    )
    out = judge_release_importance(items, llm, ReleaseImportanceConfig(), _ctx())
    assert [i.title_en for i in out] == ["b", "c"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/golden/test_release_importance.py -v`
Expected: FAIL — `ImportError: cannot import name 'judge_release_importance' from 'src.pipeline.release_importance'`

- [ ] **Step 3: 实现 `judge_release_importance()`**

在 `src/pipeline/release_importance.py` 末尾(`tier()` 函数之后)追加:

```python
import json
import re

from src.core.prompts import load_prompt
from src.core.types import RawItem, ReleaseImportanceConfig, RunContext
from src.observability.events import emit

_FULL_CHANGELOG_RE = re.compile(r"\*\*Full Changelog\*\*:\s*\S+")
_DIM_KEYS = ("scale", "refactor", "new_concept", "bugfix_only")


def _effective_body_len(raw_summary: str | None) -> int:
    """去掉 '**Full Changelog**: <url>' 比较链接后剩余的正文字符数(去首尾空白)。"""
    text = _FULL_CHANGELOG_RE.sub("", raw_summary or "")
    return len(text.strip())


def _parse_dims(raw: str) -> tuple[bool, bool, bool, bool]:
    """解析 LLM 输出的 4 个布尔维度。缺字段/非法 JSON -> 抛 ValueError (调用方 fail-open)。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM output is not a JSON object")
    if not all(k in data for k in _DIM_KEYS):
        raise ValueError(f"missing dimension keys, got {list(data.keys())}")
    return tuple(bool(data[k]) for k in _DIM_KEYS)  # type: ignore[return-value]


def build_prompt(item: RawItem, template: str) -> str:
    body = (item.raw_summary or "")[:3000]
    return template.replace("{{title}}", item.title_en).replace("{{body}}", body)


def judge_release_importance(
    items: list[RawItem], llm, config: ReleaseImportanceConfig, ctx: RunContext
) -> list[RawItem]:
    """对 adapter == "github_releases" 的条目判定重要性; 其余原样透传。
    返回硬过滤后的列表(tier <= config.hard_filter_max_tier 的条目被剔除)。"""
    emit(ctx.logger, "release_importance_start", input_count=len(items), enabled=config.enabled)
    if not config.enabled or not items:
        emit(ctx.logger, "release_importance_done", judged=0, filtered=0)
        return items

    template = load_prompt(config.prompt_path)
    out: list[RawItem] = []
    filtered = 0
    for item in items:
        if item.adapter != "github_releases":
            out.append(item)
            continue

        if _effective_body_len(item.raw_summary) < config.empty_body_min_chars:
            t = 0
        else:
            try:
                prompt = build_prompt(item, template)
                raw = llm.complete_json(
                    prompt, temperature=config.temperature, max_tokens=config.max_tokens
                )
                scale, refactor, new_concept, bugfix_only = _parse_dims(raw)
                t = tier(scale, refactor, new_concept, bugfix_only)
            except Exception as e:
                emit(
                    ctx.logger,
                    "release_importance_error",
                    link=item.link,
                    error_type=type(e).__name__,
                    error=str(e)[:200],
                )
                t = 2  # fail-open: 放行 + 中性打分, 不硬删

        if t <= config.hard_filter_max_tier:
            filtered += 1
            continue
        if t >= 2:
            item.signals["release_tier_score"] = config.tier_score.get(t, 0.0)
        out.append(item)

    emit(ctx.logger, "release_importance_done", judged=len(items) - filtered, filtered=filtered)
    return out
```

把文件顶部的 `from __future__ import annotations` 保留在最上面(已在 Task 3 写入),新增的 import 紧随其后。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/golden/test_release_importance.py -v`
Expected: PASS(8 个测试全过)

- [ ] **Step 5: 跑 `tier()` 单测确认没有回归**

Run: `uv run pytest tests/contract/test_release_importance_unit.py tests/golden/test_release_importance.py -v`
Expected: PASS(27 个测试)

- [ ] **Step 6: Commit**

```bash
git add src/pipeline/release_importance.py tests/golden/test_release_importance.py
git commit -m "feat(release-importance): add judge_release_importance with hard-filter + fail-open"
```

---

## Task 5: 生产配置(`config/enrich.yaml` + `config/scoring.yaml`)

**Files:**
- Modify: `config/enrich.yaml`
- Modify: `config/scoring.yaml`
- Test: `tests/contract/test_enrich_config.py`(追加一个"真实生产 yaml 能被 loader 正确解析"的断言)

**Interfaces:**
- Consumes: `load_enrich_config`(Task 1)、`ReleaseImportanceConfig`(Task 1)
- Produces: 无新接口,只是让 Task 1/4 的代码在生产环境实际生效

- [ ] **Step 1: 写失败测试(生产 yaml 文件可解析且字段非默认值)**

在 `tests/contract/test_enrich_config.py` 末尾追加:

```python
def test_production_enrich_yaml_has_release_importance_configured():
    cfg = load_enrich_config("config/enrich.yaml")
    ri = cfg.release_importance
    assert ri.enabled is True
    assert len(ri.models) >= 1
    assert ri.prompt_path == "src/prompts/release_importance.md"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/contract/test_enrich_config.py -v -k production_enrich`
Expected: FAIL — `assert 0 >= 1`(`config/enrich.yaml` 还没有 `release_importance` 块, `models` 为空列表默认值)

- [ ] **Step 3: 更新 `config/enrich.yaml`**

把 `config/enrich.yaml` 整个文件内容替换为:

```yaml
enabled: true
concurrency: 5
timeout_s: 8
# 这些 genre 已经带原生 popularity 信号, 不再查 HN
skip_genres: [paper, model]

# github_releases 重要性判定 (2026-07-22 设计, docs/superpowers/specs/2026-07-22-github-release-importance-design.md)
# LLM 判 4 个独立布尔维度(scale/refactor/new_concept/bugfix_only), 纯函数映射成 tier(0-3)。
# tier <= hard_filter_max_tier 直接从候选池剔除; tier >= 2 写 release_tier_score 参与打分。
release_importance:
  enabled: true
  providers:
    modelscope:
      base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
      api_key_env: "MODELSCOPE_API_KEY"
    agnes:
      base_url: "https://apihub.agnes-ai.com/v1/chat/completions"
      api_key_env: "AGNES_API_KEY"
  # 分类任务(4 个布尔值), 用 Flash 档足够, 比 interpret 主链更便宜更快
  models:
    - "modelscope:deepseek-ai/DeepSeek-V4-Flash"
  fallback_models:
    - "modelscope:moonshotai/Kimi-K2.6"
    - "agnes:agnes-2.0-flash"
  temperature: 0.1        # 分类任务, 低温度求稳定
  max_tokens: 300          # 只需 4 个布尔 + 一句 reason
  timeout_s: 30
  empty_body_min_chars: 30 # 去掉 Full Changelog 链接后正文短于此 -> 短路判 tier 0, 不调 LLM
  hard_filter_max_tier: 1  # tier <= 1 (空转/常规补丁) 从候选池剔除
  tier_score: {2: 4, 3: 9} # tier -> release_tier_score(写入 popularity_weights, sqrt+cap 压缩)
  prompt_path: "src/prompts/release_importance.md"
```

- [ ] **Step 4: 更新 `config/scoring.yaml` 的 `popularity_weights`**

在 `config/scoring.yaml` 里找到这一行:

```yaml
popularity_weights: {upvotes: 0.6, hn_points: 0.5, likes: 0.3, github_stars: 0.3, num_comments: 0.4}
```

替换为(加一个 key,注释更新):

```yaml
popularity_weights: {upvotes: 0.6, hn_points: 0.5, likes: 0.3, github_stars: 0.3, num_comments: 0.4, release_tier_score: 1.0}
# release_tier_score: judge_release_importance() 写入的 tier 分值(2/3 档), 直接用不加权(该维度本身已是策展分值, 不是原始人气数字)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/contract/test_enrich_config.py -v`
Expected: PASS(全部)

- [ ] **Step 6: 跑 `score.py` 相关 golden 测试确认 `release_tier_score` 不破坏既有打分**

Run: `uv run pytest tests/golden/ -v -k score`
Expected: PASS(既有 score golden 测试不用新 key,不受影响)

- [ ] **Step 7: Commit**

```bash
git add config/enrich.yaml config/scoring.yaml tests/contract/test_enrich_config.py
git commit -m "feat(config): wire release_importance model chain + release_tier_score weight"
```

---

## Task 6: `cli.py` 接入(collect → enrich_with_hn → judge_release_importance → dedup)

**Files:**
- Modify: `src/cli.py`(两处调用点:`_dry_run_prefix` 约第 108-131 行、`run_tick` 内的 `_collect_and_interpret` 约第 405-424 行;import 块约第 14-41 行)

**Interfaces:**
- Consumes: `judge_release_importance`(Task 4)、`ReleaseImportanceConfig`(Task 1)、`OpenAICompatLLM`(既有)
- Produces: `_make_release_importance_llm(cfg: ReleaseImportanceConfig) -> OpenAICompatLLM`(新增私有 helper,`_make_llm` 的姊妹函数)

这个任务是纯 glue code 接线,production 路径(`run_tick`)已有的 `enrich_with_hn` 接入本身也没有独立的 cli 级别测试(只在 `test_enrich.py` 测函数本身),这里跟随同样的既有测试覆盖惯例——正确性由 Task 4 的 golden 测试保证,这里只保证接线不出 import/语法错误、不破坏现有 cli 测试。

- [ ] **Step 1: 加 import**

在 `src/cli.py` 的 import 块(约第 32 行)里,把:

```python
from src.core.types import CollectionConfig, InterpretConfig, ProviderSpec, RunContext
```

改为:

```python
from src.core.types import (
    CollectionConfig,
    InterpretConfig,
    ProviderSpec,
    ReleaseImportanceConfig,
    RunContext,
)
```

再找到(约第 39 行):

```python
from src.pipeline.enrich import enrich_with_hn
```

改为:

```python
from src.pipeline.enrich import enrich_with_hn
from src.pipeline.release_importance import judge_release_importance
```

- [ ] **Step 2: 加 `_make_release_importance_llm` helper**

在 `src/cli.py` 里紧跟 `_make_llm`(约第 61-72 行)之后插入:

```python
def _make_release_importance_llm(cfg: ReleaseImportanceConfig) -> OpenAICompatLLM:
    if cfg.models:
        primary = cfg.models[0]
        fallbacks = cfg.models[1:] + cfg.fallback_models
    else:
        primary = cfg.model
        fallbacks = cfg.fallback_models
    return OpenAICompatLLM(
        providers=cfg.providers,
        model=primary,
        timeout_s=cfg.timeout_s,
        fallback_models=fallbacks,
    )
```

- [ ] **Step 3: 接入 `_dry_run_prefix`**

把 `_dry_run_prefix` 的签名(约第 108-115 行)：

```python
def _dry_run_prefix(
    registry_path: str,
    ctx: RunContext,
    embedder=None,
    llm=None,
    *,
    enrich: bool = False,
    stop_at: str = "interpret",
):
```

改为(加一个 `release_llm` 可选注入参数):

```python
def _dry_run_prefix(
    registry_path: str,
    ctx: RunContext,
    embedder=None,
    llm=None,
    release_llm=None,
    *,
    enrich: bool = False,
    stop_at: str = "interpret",
):
```

把函数体内的:

```python
    if enrich:
        ecfg = load_enrich_config("config/enrich.yaml")

        async def _collect_then_enrich():
            c = await collect(coll_cfg, ctx)
            if ecfg.enabled and c.items:
                await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
            return c

        coll = asyncio.run(_collect_then_enrich())
```

改为:

```python
    if enrich:
        ecfg = load_enrich_config("config/enrich.yaml")

        async def _collect_then_enrich():
            c = await collect(coll_cfg, ctx)
            if ecfg.enabled and c.items:
                await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
            if ecfg.release_importance.enabled and c.items:
                ri_llm = release_llm or _make_release_importance_llm(ecfg.release_importance)
                c.items = judge_release_importance(c.items, ri_llm, ecfg.release_importance, ctx)
            return c

        coll = asyncio.run(_collect_then_enrich())
```

- [ ] **Step 4: 接入 `run_tick`(生产路径)**

在 `run_tick` 内的 `_collect_and_interpret`(约第 408-414 行)把:

```python
    async def _collect_and_interpret():
        c = await collect(coll_cfg, ctx)
        if ecfg.enabled and c.items:
            await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
        dcfg2 = load_dedup_config("config/dedup.yaml")
```

改为:

```python
    async def _collect_and_interpret():
        c = await collect(coll_cfg, ctx)
        if ecfg.enabled and c.items:
            await enrich_with_hn(c.items, HNAlgoliaClient(ecfg.timeout_s), ecfg, ctx)
        if ecfg.release_importance.enabled and c.items:
            ri_llm = _make_release_importance_llm(ecfg.release_importance)
            c.items = judge_release_importance(c.items, ri_llm, ecfg.release_importance, ctx)
        dcfg2 = load_dedup_config("config/dedup.yaml")
```

- [ ] **Step 5: 跑全量测试确认没有破坏任何既有行为**

Run: `uv run pytest tests/ -v`
Expected: PASS(全部既有测试 + 本计划新增的全部测试)

- [ ] **Step 6: 跑 lint(CI 会跑,这里本地先过一遍)**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: 无输出(全部通过);若 `ruff format --check` 报格式问题,跑 `uv run ruff format .` 后重新 `git diff` 确认改动仍然精确,再重新跑 check。

- [ ] **Step 7: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): wire judge_release_importance into collect->enrich pipeline"
```

---

## 收尾

- [ ] **Step 1: 跑一次真实 dry-run 冒烟(需要 `MODELSCOPE_API_KEY` 环境变量,参考 [[run-real-dryrun-diagnostics]] memory)**

Run: `uv run python -m src.cli dry-run-collect --registry config/sources.yaml --enrich 2>&1 | tail -50`(具体子命令名以 `src/cli.py` 现有 argparse 定义为准,若无直接对应的 `--enrich` flag 则用 `run_collect_tick` 走一次完整 tick 的 dry 模式)

Expected: 日志里能看到 `release_importance_start`/`release_importance_done` 事件,`filtered` 数值 > 0(说明真的拦下了一些 release)。

- [ ] **Step 2: 开 PR**

分支 `spec/github-release-importance` 已经 ahead of `origin/master`(含 spec commit)。这次实施完成后:

```bash
git push -u origin spec/github-release-importance
gh pr create --title "feat(release-importance): LLM 判定 github_releases 重要性, 硬过滤刷屏 patch" --body "$(cat <<'EOF'
## Summary
- github_releases 打分之前只有 repo 级常数 star, 同一 repo 一天多条 patch release 拿同样高分刷屏候选池(#61 的 adapter_quota 只在最终发布生效, 候选池不受保护)。
- 新增 enrich 层判定: LLM 判 4 个独立布尔维度(scale/refactor/new_concept/bugfix_only), 纯函数 tier() 映射到 0-3 档, tier<=1 硬过滤, tier>=2 写 release_tier_score 参与打分(复用现有 popularity_weights)。
- rubric 用 ComfyUI 真实 release 校准, 跨 10 个 repo(含今天投诉的 cline 4 条 spam)验证。
- 详见 docs/superpowers/specs/2026-07-22-github-release-importance-design.md

## Test plan
- [x] tier() 纯函数单测覆盖全部 16 种布尔组合
- [x] judge_release_importance golden 测试: 硬过滤 / 打分注入 / fail-open 容错 / 非 release adapter 透传
- [x] config loader 契约测试: 默认值 / yaml 覆盖 / 生产 yaml 可解析
- [x] uv run pytest tests/ 全绿
- [x] uv run ruff check + format --check 全绿
- [ ] 真实 dry-run 冒烟(需要 MODELSCOPE_API_KEY)
EOF
)"
```

repo 清单更新/权重重排(spec 中列为"非目标")留后续单开 PR,不在这个分支处理。
