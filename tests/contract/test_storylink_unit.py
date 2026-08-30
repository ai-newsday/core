from src.pipeline.storylink import extract_entity_tokens

DEFAULT_PATTERN = r"\b[A-Za-z]+[-\s]?\d+(?:\.\d+)*\b"


def test_extract_entity_tokens_hyphenated_version():
    assert extract_entity_tokens("GLM-5.3 released today", DEFAULT_PATTERN) == {"GLM-5.3"}


def test_extract_entity_tokens_attached_version():
    assert extract_entity_tokens("Upgrade to v0.28.0 now", DEFAULT_PATTERN) == {"V0.28.0"}


def test_extract_entity_tokens_spaced_version():
    assert extract_entity_tokens("Llama 4 is here", DEFAULT_PATTERN) == {"LLAMA-4"}


def test_extract_entity_tokens_normalizes_case_and_separator():
    a = extract_entity_tokens("glm 5.3 dropped", DEFAULT_PATTERN)
    b = extract_entity_tokens("GLM-5.3 dropped", DEFAULT_PATTERN)
    assert a == b == {"GLM-5.3"}


def test_extract_entity_tokens_multiple_hits():
    text = "GLM-5.3 now supported alongside Llama 4 in v0.28.0 of the toolkit"
    tokens = extract_entity_tokens(text, DEFAULT_PATTERN)
    assert tokens == {"GLM-5.3", "LLAMA-4", "V0.28.0"}


def test_extract_entity_tokens_no_hits_returns_empty_set():
    assert (
        extract_entity_tokens("A generic announcement with no version numbers", DEFAULT_PATTERN)
        == set()
    )


def test_extract_entity_tokens_empty_text():
    assert extract_entity_tokens("", DEFAULT_PATTERN) == set()
