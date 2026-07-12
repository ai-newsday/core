# 翻译治根 (multi-provider LLM + max_tokens + telemetry) — 设计

日期: 2026-07-11 · 触发: KANBAN §3 P0 "翻译失效根治"; 实测 fallback_rate = 100% (2026-07-11 run 25/25 全 extractive_fallback), 用户反馈 post 全英文/垃圾

## 根因 (今日诊断实测得出, 不猜)

三个 bug 叠加, 每一个单独就能让整个 pipeline 出英文:

**A. ModelScope 4 个模型已下架** — `config/interpret.yaml` 里 primary 首个 `MiniMax/MiniMax-M2.7` + `ZhipuAI/GLM-5.1`, fallback 首个 `MiniMax/MiniMax-M2.5` + `ZhipuAI/GLM-5` 全返 `400 "Model id has no provider supported"`。链条前 2/5 主模型 + 前 2/9 备胎全废。

**B. `max_tokens=800` 不够 DeepSeek 生成完整 JSON** — 实测 `deepseek-ai/DeepSeek-V4-Pro` 生成 body 超过 prompt 要求的"≤180 字", `finish_reason=length` 顶到 801 tokens 截断 → JSON 残缺 → parse 失败。虽然模型自己不遵守长度约束, 但 `max_tokens` 给足就能拿到合法输出。

**C. parse 失败不触发链上下一个模型** — `complete_json` 只在 HTTP 层出错时切下一模型; parse_and_validate 在外层, 一失败直接 extractive_fallback, 剩下 8 个能用的备胎完全没被试。

## 目标 / 验收

1. **今日 25/25 全 fallback → fallback_rate 大幅下降** (预期 <10%, 但**不定死具体值**, 用 metrics 观察)
2. **零基础设施**: 复用现有 LLM 适配器 + config + metrics dashboard, 不引 daemon / 服务器
3. **失败保险丝**: Agnes AI 作为 ModelScope 全挂时的最后一档 (付费, 极罕见触发, 一天成本 ≈ 0)
4. **可诊断**: `fallback_reason` 字段 + metrics chart 让**下次同类问题图上就能定位**, 不用手工重跑
5. **不挂**: 全链失败仍走 extractive_fallback, 保底出内容 (即使全英文)

## 设计

### §1 拓扑

```
每条 item 依次尝试:
  1-4. ModelScope alive (curated):
       ├─ deepseek-ai/DeepSeek-V4-Pro
       ├─ inclusionAI/Ring-2.6-1T
       ├─ deepseek-ai/DeepSeek-V4-Flash
       └─ inclusionAI/Ling-2.6-1T
  5-7. ModelScope 未探活 (留观察, 若挂就下架):
       ├─ moonshotai/Kimi-K2.6
       ├─ moonshotai/Kimi-K2.5
       └─ Qwen/Qwen3.5-397B-A17B
  8. Agnes: agnes-2.0-flash (付费保险丝, 只在 ModelScope 全挂时调)
  ─ 还挂 ─
  9. extractive_fallback (保底出内容)
```

**从 config 删除** (今日探活 400): `MiniMax/MiniMax-M2.7`, `ZhipuAI/GLM-5.1`, `MiniMax/MiniMax-M2.5`, `ZhipuAI/GLM-5`, `MiniMax/MiniMax-M2.7-Fallback` (若存在), `Shanghai_AI_Laboratory/Intern-S1-Pro`, `Shanghai_AI_Laboratory/Intern-S2-Preview` (spec 保留 Kimi + Qwen 未探活 3 个, 由第一次真调用探活)

**Agnes 定位理由**: user 确认付费; ModelScope 修好后 fallback_rate 应大幅降; Agnes 一天预期 0-3 次调用, 成本可控。**不当主力**。

**`models` vs `fallback_models` 语义**: 现有 LLM 适配器把两者按 `[*models, *fallback_models]` 顺序串成一条链依次试。**新 spec 沿用**这个语义, 但边界重新划分: `models` = 今日探活 4 家 alive 的 curated primary; `fallback_models` = 未探活 3 家 + Agnes 尾保险丝。语义上没变 (还是"顺序试, 谁先成谁赢"), 只是清晰把"信心高"和"信心低"分开。

### §2 config schema — 多 provider

`config/interpret.yaml` 引入 `providers` block + `<provider>:<model>` 前缀:

```yaml
providers:
  modelscope:
    base_url: "https://api-inference.modelscope.cn/v1/chat/completions"
    api_key_env: "MODELSCOPE_API_KEY"
  agnes:
    base_url: "https://apihub.agnes-ai.com/v1/chat/completions"
    api_key_env: "AGNES_API_KEY"

# primary = 今日探活证实 alive 的 4 家
models:
  - "modelscope:deepseek-ai/DeepSeek-V4-Pro"
  - "modelscope:inclusionAI/Ring-2.6-1T"
  - "modelscope:deepseek-ai/DeepSeek-V4-Flash"
  - "modelscope:inclusionAI/Ling-2.6-1T"

# fallback = 未探活 3 家 (第一次调用见分晓) + Agnes 付费保险丝
fallback_models:
  - "modelscope:moonshotai/Kimi-K2.6"
  - "modelscope:moonshotai/Kimi-K2.5"
  - "modelscope:Qwen/Qwen3.5-397B-A17B"
  - "agnes:agnes-2.0-flash"          # 尾保险丝, 只在前 7 家全挂时调

temperature: 0.3
max_tokens: 1500                     # 从 800 上调, 治 DeepSeek 截断
timeout_s: 60
title_max_chars: 64
body_max_chars: 240
tags_count: 3
min_evidence: 1
item_prompt_path: "src/prompts/interpret_item.md"
daily_prompt_path: "src/prompts/daily_take.md"
```

### §3 `InterpretConfig` 类型改动

`src/core/types.py`:
```python
class ProviderSpec(BaseModel):
    base_url: str
    api_key_env: str

class InterpretConfig(BaseModel):
    providers: dict[str, ProviderSpec]     # 新
    models: list[str]                       # 值形如 "modelscope:deepseek-ai/…"
    fallback_models: list[str]
    # ... 其余保持
```

向后兼容: `load_interpret_config` 里若 yaml 无 `providers` block, 用默认 (只含 modelscope 默认 URL) —— 现有 golden test 不砍。

### §4 `OpenAICompatLLM` 改多 provider

`src/adapters/llm/openai_compat.py`:

```python
class OpenAICompatLLM:
    def __init__(
        self, providers: dict[str, ProviderSpec], model: str, timeout_s: int,
        fallback_models: list[str] | None = None,
    ):
        self._providers = providers
        self._provider_keys = {name: os.environ.get(p.api_key_env, "") for name, p in providers.items()}
        self._model = model
        self._fallback_models = fallback_models or []
        self._timeout = timeout_s

    def _call(self, model_ref: str, prompt: str, *, temperature: float, max_tokens: int) -> str:
        provider, model_id = model_ref.split(":", 1)
        prov = self._providers[provider]
        key = self._provider_keys[provider]
        if not key:
            raise ValueError(f"missing API key for provider {provider} (env {prov.api_key_env})")
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(
                prov.base_url,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model_id, "messages": [{"role": "user", "content": prompt}],
                      "temperature": temperature, "max_tokens": max_tokens},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            if not content:
                raise ValueError(f"model {model_ref} returned empty content")
            return content

    def complete_json(
        self, prompt: str, *, temperature: float, max_tokens: int,
        validator: Callable[[str], Any] | None = None,
    ) -> str:
        models = [self._model] + self._fallback_models
        last_err: Exception | None = None
        for model_ref in models:
            try:
                raw = self._call(model_ref, prompt, temperature=temperature, max_tokens=max_tokens)
                if validator:
                    validator(raw)  # parse 失败视为该 model 失败
                if model_ref != self._model:
                    logger.info("LLM fallback: %s succeeded (primary %s failed)", model_ref, self._model)
                return raw
            except Exception as e:
                logger.warning("LLM %s failed: %s", model_ref, e)
                last_err = e
        raise last_err  # type: ignore[misc]
```

关键改动:
1. 构造函数接受 `providers` dict 而不是单 base_url
2. `_call` 从 `<provider>:<model>` 拆前缀选 base_url + key
3. `complete_json` 加 `validator` 参数, parse 失败当模型失败 (核心 §C 修复)

### §5 `parse_and_validate` 作为 validator 传入

`src/pipeline/interpret.py`:
```python
def interpret_item(item, item_template, config, llm, logger=None) -> InterpretedItem:
    try:
        prompt = build_item_prompt(item, item_template)
        # parse_and_validate 双职务: 既作 LLM 链的 validator, 又给 build_ok_item
        parsed_holder = {}
        def _validate(raw: str):
            parsed_holder["parsed"] = parse_and_validate(raw)
        raw = llm.complete_json(
            prompt, temperature=config.temperature, max_tokens=config.max_tokens,
            validator=_validate,
        )
        parsed = parsed_holder["parsed"]
        return build_ok_item(parsed, item, config)
    except Exception as e:
        if logger is not None:
            emit(logger, "interpret_error", link=item.link,
                 error_type=type(e).__name__, error=str(e)[:200])
        return extractive_fallback(item, config, fallback_reason=type(e).__name__)
```

### §6 `fallback_reason` 字段 (telemetry)

`InterpretedItem` (`src/core/types.py`) 加:
```python
class InterpretedItem(ScoredItem):
    ...
    fallback_reason: str | None = None  # exception type on fallback, None on ok
```

`extractive_fallback` 签名加参数:
```python
def extractive_fallback(item, config, *, fallback_reason: str | None = None) -> InterpretedItem:
    ...
    return InterpretedItem(
        ...,
        interpretation_status="extractive_fallback",
        fallback_reason=fallback_reason,
    )
```

### §7 metrics dashboard 加 breakdown

`src/pipeline/metrics.py` 加纯函数:
```python
def compute_fallback_breakdown(run_dir: Path) -> dict[str, int]:
    """Count fallback items by fallback_reason from 04_interpreted.jsonl.
    Returns e.g. {"ValueError": 10, "HTTPStatusError": 5, "unknown": 2}."""
    counter: Counter = Counter()
    for row in _iter_rows(run_dir / "04_interpreted.jsonl"):
        if row.get("interpretation_status") == "extractive_fallback":
            reason = row.get("fallback_reason") or "unknown"
            counter[reason] += 1
    return dict(counter)
```

`src/pipeline/metrics_render.py`:
- JSON `data.fallback_breakdown` 新字段
- `render_md` 表格加 "## fallback 分类" 一节
- `render_caption` 若 breakdown 非空, caption 加一行 `top fail: ValueError × 10`
- `render_png` **不改** (subplot 已经 2 个, 加第三个太挤; breakdown 靠 md 和 caption 就够)

### §8 环境变量 + secrets

- 本地: `~/.zshrc` 加 `export AGNES_API_KEY=<新 key>` (**用户去 Agnes dashboard 撤销旧 key + 生成新 key**)
- GH Actions: repo Settings → Secrets → New: `AGNES_API_KEY`
- `.github/workflows/collect.yml`: env 加 `AGNES_API_KEY: ${{ secrets.AGNES_API_KEY }}`
- `.github/workflows/finalize.yml`: 同上

### §9 失败降级

| 故障 | 行为 |
|---|---|
| ModelScope 4 家全 API 挂 | 走 Agnes fallback (预期 <10% 事件) |
| Agnes 也挂 (key 无效 / 服务下线) | extractive_fallback 保底, 日报仍出 |
| 全链模型都返 verbose 超 max_tokens | 每次 model 都 parse 失败切下一个, 最后 extractive_fallback (与今日相同, 但 `fallback_reason=ValueError` 记录清楚, 下轮修 prompt 或再提 max_tokens) |
| `AGNES_API_KEY` 未配 | Agnes call 抛 ValueError → 记 warning → 试下一 (但下一是 extractive_fallback), pipeline 不挂 |
| Agnes rate limit / 429 | HTTPStatusError → extractive_fallback, 后续通过 telemetry 看到再考虑加 retry |

## 替代方案 (拒)

| 方案 | 拒因 |
|---|---|
| Agnes 当主力, ModelScope 备胎 | 用户已确认 Agnes 付费, 每天 25+ 条全走 Agnes 成本不受控 |
| 只删 4 个死模型 + 提 max_tokens (§A+§B 不做 §C) | 快 3 行 yaml, 但下次 DeepSeek 又出别的问题 (verbose/refuse) 你还得手工诊断; 治标不治本 |
| 保留 MiniMax/GLM 死模型不删 | 每次调 API 白白等 400, 拖慢每条 item |
| 用 Google 官方 Gemini API 替代 Agnes | 用户手上没 Google API key; 国内 GH Actions 直连 Google 有网络问题 |
| 加 per-model retry (fail 后重试同一 model N 次) | HTTPStatusError 400 / ValueError 没有 retry 意义; 只对 429 / timeout 有意义, 但今天证据不是这类, YAGNI |
| 收紧 prompt 让 DeepSeek 遵守 ≤180 字 | 模型不受控, 不同模型对约束遵守程度不同; 治不了根 |
| max_tokens 拉到 3000 (更保险) | Agnes 按 output tokens 计费, 上限越高兜底调用越贵 (虽然 finish_reason=stop 会截断, 但 max=3000 允许失控的 verbose 完成) |

## 实施顺序 (建议 5 commit 单 PR)

| # | 内容 | 验证 |
|---|---|---|
| **C1** | `ProviderSpec` 类型 + `InterpretConfig.providers` + `load_interpret_config` 向后兼容 (无 providers 时用 modelscope 默认) + contract test | pytest 全绿, 现有 golden 不砍 |
| **C2** | `OpenAICompatLLM` 改多 provider + `<provider>:<model>` 拆分 + `_call` 分派 + contract test | pytest 全绿 |
| **C3** | `complete_json` 加 `validator` 参数 + `interpret_item` 传 `parse_and_validate` 作 validator + contract test (mock LLM 让 primary 通过 API 但 parse 失败, 断言切下一 model 成功) | pytest 全绿 |
| **C4** | `InterpretedItem.fallback_reason` + `extractive_fallback` 参数 + metrics `compute_fallback_breakdown` + metrics_render `render_md/render_caption` 显示 breakdown + 单元测 | pytest 全绿 |
| **C5** | `config/interpret.yaml` 改多 provider + 删死模型 + max_tokens 1500 + `.github/workflows/{collect,finalize}.yml` 加 `AGNES_API_KEY` env; 本地 dry-run smoke 验 fallback_reason 落对 | dry-run + 手 diff 04_interpreted.jsonl 确认 |

## 测试矩阵

| 层 | 测试 | 类型 |
|---|---|---|
| `load_interpret_config` | providers block 存在 → 正常加载; 无 → default modelscope | contract |
| `OpenAICompatLLM._call` | `<provider>:<model>` 拆分正确; provider 不存在 → KeyError | 单元 |
| `OpenAICompatLLM.complete_json` | validator 传入, 模拟 primary parse 失败 → 切下一 model → 成功 | 单元 (respx mock) |
| `OpenAICompatLLM.complete_json` | 全模型 API 挂 → 抛最后一个 error | 单元 |
| `interpret_item` | LLM 全挂 → fallback + `fallback_reason` 填 exception type | golden |
| `extractive_fallback` | 显式传 fallback_reason 或不传, 都不挂 | 单元 |
| `metrics.compute_fallback_breakdown` | fixture 3 fallback (2 ValueError + 1 HTTPStatusError) → {"ValueError": 2, "HTTPStatusError": 1} | 单元 |
| `metrics_render.render_md` | breakdown 非空 → md 里有 "fallback 分类" 表 | snapshot |
| `metrics_render.render_caption` | breakdown 非空 → caption 加一行 `top fail: X × N` | 单元 |
| e2e | 手改 config 让 primary 故意 400 → dry-run → 观察 04_interpreted 里 fallback_reason 落 `HTTPStatusError` | integration |

## 不做 (YAGNI)

- Per-provider rate limit / concurrency control (fail-move-on 保底)
- Agnes / ModelScope 健康检查 endpoint (第一次调用失败就自然切)
- Pricing telemetry (Agnes 单价 + 每日调用数) — 等真花超才加
- Multi-model 并发调用取最快 (顺序足够, 加复杂度)
- 支持 Google 官方 Gemini SDK (Agnes 是 OpenAI-兼容够用了)
- fallback_reason 全类别分类 (只 count exception type, 不做正则匹配 raw error msg)

## 后续可加

- ponytail: 只统计 fallback_reason 的 exception type. 升级路径 = 若某类占比过高 (e.g. HTTPStatusError > 30%), 再加 status code 细分 (400 vs 429 vs 500)
- ponytail: 现有 config 无版本控制. 升级路径 = 若多用户/多环境, 加 provider 覆盖机制 (env 里指定 config-override)
- ponytail: Agnes 一天 0-3 次调用无 cost 追踪. 升级路径 = 若发现 Agnes 用量爆炸, 在 metrics 里加"付费 provider 调用次数"计数

## 关联

- KANBAN §3 P0 "翻译失效根治" — 本 spec (完成后打钩)
- KANBAN §3 P0 "产品质量 metrics dashboard" — 已 shipped, 本 spec 顺手扩展 metrics 加 fallback_reason breakdown
- Memory `modelscope-model-registry` — 本 spec 落地后更新 memory: 记录 2026-07-11 探活结果 (哪些 alive / dead), 未来 config 改动时先查这条
- Memory `run-real-dryrun-diagnostics` — 本 spec 里"实施顺序"C5 的 smoke 走 `--dry-run` (不动 db, 只看 04_interpreted.jsonl)
