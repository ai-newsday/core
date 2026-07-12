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
