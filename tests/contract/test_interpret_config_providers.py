import yaml

from src.core.config import load_interpret_config
from src.core.types import ProviderSpec


def test_providers_default_when_yaml_lacks_block(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(yaml.safe_dump({"temperature": 0.3, "max_tokens": 800}))
    cfg = load_interpret_config(str(p))
    assert "modelscope" in cfg.providers
    ms = cfg.providers["modelscope"]
    assert isinstance(ms, ProviderSpec)
    assert ms.base_url == "https://api-inference.modelscope.cn/v1/chat/completions"
    assert ms.api_key_env == "MODELSCOPE_API_KEY"


def test_providers_block_parsed_from_yaml(tmp_path):
    p = tmp_path / "interpret.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "modelscope": {
                        "base_url": "https://api-inference.modelscope.cn/v1/chat/completions",
                        "api_key_env": "MODELSCOPE_API_KEY",
                    },
                    "agnes": {
                        "base_url": "https://apihub.agnes-ai.com/v1/chat/completions",
                        "api_key_env": "AGNES_API_KEY",
                    },
                },
            }
        )
    )
    cfg = load_interpret_config(str(p))
    assert set(cfg.providers.keys()) == {"modelscope", "agnes"}
    assert cfg.providers["agnes"].base_url == "https://apihub.agnes-ai.com/v1/chat/completions"
    assert cfg.providers["agnes"].api_key_env == "AGNES_API_KEY"


def test_providers_default_when_yaml_missing_file(tmp_path):
    # Missing file → all defaults, providers still has modelscope
    cfg = load_interpret_config(str(tmp_path / "does-not-exist.yaml"))
    assert "modelscope" in cfg.providers


# 2026-09-10 实测: 这些模型 id 在 ModelScope 上返回
#   400 {"error":{"message":"Model id : X , has no provider supported"}}
# 模型不存在, 不是负载问题。当晚它们贡献 161 次调用、0 次成功, 占全部失败的 41%,
# 而每个条目都要挨个撞完才轮到能用的模型。
_NONEXISTENT_MODELS = [
    "deepseek-ai/DeepSeek-V4-Flash",
    "inclusionAI/Ling-2.6-1T",
    "moonshotai/Kimi-K2.6",
    "moonshotai/Kimi-K2.5",
]


def test_no_chain_references_a_nonexistent_model():
    """任何一条模型链都不该再引用这些 id。删掉是因为**实测不存在**, 不是因为慢。

    注意区分: DeepSeek-V4-Pro / Ring-2.6-1T / Qwen3.5-397B-A17B 是**间歇性**的
    (单次探活全空、同一晚却成功 27 次), 所以刻意保留 —— 按单次采样删掉会砍掉
    当晚 30 次成功里的 26 次。"""
    from src.core.config import (
        load_enrich_config,
        load_interpret_config,
        load_storylink_config,
    )

    chains = {
        "interpret": load_interpret_config("config/interpret.yaml"),
        "storylink": load_storylink_config("config/storylink.yaml"),
    }
    ecfg = load_enrich_config("config/enrich.yaml")
    chains["release_importance"] = ecfg.release_importance

    for name, cfg in chains.items():
        refs = list(getattr(cfg, "models", []) or []) + list(
            getattr(cfg, "fallback_models", []) or []
        )
        for bad in _NONEXISTENT_MODELS:
            assert not any(bad in r for r in refs), f"{name} 链里仍引用不存在的 {bad}"
