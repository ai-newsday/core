import httpx
import respx

from src.tools.provider_models import parse_models, probe_one

URL = "https://apihub.agnes-ai.com/v1"


def test_parse_models_takes_ids_from_the_openai_shape():
    data = {"data": [{"id": "agnes-2.0-flash"}, {"id": "agnes-3.0-pro"}, {"nope": 1}]}
    assert parse_models(data) == ["agnes-2.0-flash", "agnes-3.0-pro"]


@respx.mock
def test_probe_reports_ok_rate_limited_and_missing():
    """列出来不等于能用: ModelScope 列了 46 个模型, 其中 4 个调用直接 400。
    限流(429)也要跟"不存在"分开——2026-09-19 实测 agnes 的限额是按模型算的,
    被限的模型晚点还能用, 不该从名单里删掉。"""
    respx.post(f"{URL}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}),
            httpx.Response(429, json={"error": {"message": "rate limit"}}),
            httpx.Response(400, json={"error": {"message": "no provider"}}),
        ]
    )
    assert probe_one(f"{URL}/chat/completions", "k", "m1")[0] == "ok"
    assert probe_one(f"{URL}/chat/completions", "k", "m2")[0] == "rate_limited"
    assert probe_one(f"{URL}/chat/completions", "k", "m3")[0] == "error"
