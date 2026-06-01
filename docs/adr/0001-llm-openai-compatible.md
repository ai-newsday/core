# ADR 0001 — 解读层 LLM 走 OpenAI 兼容端点（而非 Anthropic 原生）

- 状态：已接受
- 日期：2026-06-01
- 关联：`docs/specs/interpret.md`、CLAUDE.md「技术栈（已锁定）· LLM：Claude」

## 背景
CLAUDE.md 锁定「LLM：Claude（便宜步用 Haiku，解读用 Sonnet），走 provider 适配器可换」。
Circle 4 解读层是首个引入 LLM 的层。

## 决策
MVP 阶段解读层真实适配器走 **OpenAI 兼容 `/chat/completions` 端点**（默认 ModelScope），
复用 embedding 已有的 `MODELSCOPE_API_KEY` 与 httpx 调用方式。

## 理由
- 复用同一套认证/SDK，降低接入与成本（embedding 已在 ModelScope）。
- `LLMProvider` 是 Protocol，业务层只依赖契约；换回 Anthropic 原生只需新增一个适配器，不动 orchestrator。
- 结构化 JSON 输出 + schema 校验 + 抽取式回退的纪律与具体厂商无关。

## 后果
- 默认模型为可配置的 OpenAI 兼容 chat 模型（`config/interpret.yaml: model`）。
- 后续如需 Anthropic 原生（Sonnet/Haiku），新增 `src/adapters/llm/anthropic.py` 实现同一 `LLMProvider` 协议即可，无需改本层逻辑。
