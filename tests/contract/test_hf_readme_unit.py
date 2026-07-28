from src.pipeline.hf_readme import _clean_readme, _model_id_from_link


def test_clean_readme_strips_yaml_frontmatter():
    text = "---\nlicense: mit\ntags:\n  - foo\n---\n\nReal prose starts here."
    assert _clean_readme(text) == "Real prose starts here."


def test_clean_readme_strips_images():
    text = "Intro line.\n\n![demo](https://example.com/demo.png)\n\nMore prose."
    out = _clean_readme(text)
    assert "![" not in out
    assert "Intro line." in out
    assert "More prose." in out


def test_clean_readme_strips_html_tags():
    text = (
        '<h1 align="center">Title</h1>\n\n<p align="center"><a href="x">badge</a></p>\n\nBody text.'
    )
    out = _clean_readme(text)
    assert "<h1" not in out and "<p" not in out and "<a" not in out
    assert "Body text." in out


def test_clean_readme_collapses_blank_lines():
    text = "Para one.\n\n\n\n\nPara two."
    out = _clean_readme(text)
    assert "\n\n\n" not in out


def test_clean_readme_empty_input_returns_empty():
    assert _clean_readme("") == ""
    assert _clean_readme("   \n\n  ") == ""


def test_clean_readme_frontmatter_only_returns_empty():
    text = "---\nlicense: mit\n---\n"
    assert _clean_readme(text) == ""


def test_model_id_from_link_strips_hf_prefix():
    assert (
        _model_id_from_link("https://huggingface.co/microsoft/Mage-Flow") == "microsoft/Mage-Flow"
    )
    assert (
        _model_id_from_link("https://huggingface.co/unsloth/Laguna-S-2.1-GGUF")
        == "unsloth/Laguna-S-2.1-GGUF"
    )
