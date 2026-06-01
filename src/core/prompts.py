from __future__ import annotations


def load_prompt(path: str) -> str:
    """Read a prompt template verbatim (runtime-loaded SOP, not hardcoded).
    Templates use {{name}} double-brace placeholders so JSON braces are untouched."""
    with open(path, encoding="utf-8") as f:
        return f.read()
