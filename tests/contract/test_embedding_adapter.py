import httpx, respx, pytest
from src.adapters.embedding.modelscope import ModelScopeEmbedder

URL = "https://api-inference.modelscope.cn/v1/embeddings"


def _resp(vectors):
    return httpx.Response(200, json={"data": [{"embedding": v} for v in vectors]})


@respx.mock
def test_embed_returns_vectors_in_order():
    respx.post(URL).mock(return_value=_resp([[1.0, 0.0], [0.0, 1.0]]))
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    out = emb.embed(["a", "b"])
    assert out == [[1.0, 0.0], [0.0, 1.0]]


@respx.mock
def test_embed_batches_by_batch_size():
    route = respx.post(URL).mock(side_effect=[
        _resp([[1.0]]), _resp([[2.0]])])
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=1)
    out = emb.embed(["a", "b"])
    assert out == [[1.0], [2.0]]
    assert route.call_count == 2


@respx.mock
def test_embed_raises_on_http_error():
    respx.post(URL).mock(return_value=httpx.Response(500))
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    with pytest.raises(Exception):
        emb.embed(["a"])


def test_embed_empty_returns_empty():
    emb = ModelScopeEmbedder(api_key="k", model="m", batch_size=32)
    assert emb.embed([]) == []
