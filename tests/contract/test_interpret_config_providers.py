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
# 模型不存在, 不是负载问题。当晚它们贡献 161 次调用、0 次成功, 占全部失败的 41%。
_NONEXISTENT_MODELS = [
    "deepseek-ai/DeepSeek-V4-Flash",
    "inclusionAI/Ling-2.6-1T",
    "moonshotai/Kimi-K2.6",
    "moonshotai/Kimi-K2.5",
]


def _chain_refs():
    from src.core.config import load_enrich_config, load_interpret_config, load_storylink_config

    chains = {
        "interpret": load_interpret_config("config/interpret.yaml"),
        "storylink": load_storylink_config("config/storylink.yaml"),
        "release_importance": load_enrich_config("config/enrich.yaml").release_importance,
    }
    return {
        name: list(getattr(c, "models", []) or []) + list(getattr(c, "fallback_models", []) or [])
        for name, c in chains.items()
    }


def test_no_chain_references_a_nonexistent_model():
    """删掉是因为**实测不存在**, 不是因为慢。

    注意: 带日期后缀的 id 不在此列 —— `DeepSeek-V4-Flash-0731` 是存在的(ModelScope
    给模型 id 加日期后缀, 无后缀别名会失效), 所以这里按完整 id 精确比对, 不做子串匹配。"""
    for name, refs in _chain_refs().items():
        ids = {r.split(":", 1)[-1] for r in refs}
        for bad in _NONEXISTENT_MODELS:
            assert bad not in ids, f"{name} 链里仍引用不存在的 {bad}"


def test_intermittent_modelscope_models_are_deliberately_kept():
    """把"不要因为看起来死了就删"钉住。

    DeepSeek-V4-Pro / Ring-2.6-1T / Qwen3.5-397B-A17B 单次探活全返回零值 200(跟死了
    一模一样), 同一晚生产日志里却成功了 27 次(26 次 DeepSeek-V4-Pro)。按单次采样删掉
    会砍掉当晚 30 次解读成功里的 26 次。它们留给定期维护的可用名单去管。"""
    refs = _chain_refs()["interpret"]
    assert "modelscope:deepseek-ai/DeepSeek-V4-Pro" in refs
