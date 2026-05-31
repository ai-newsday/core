"""Append HN-popularity OPML feeds to config/sources.yaml as status: manual.
Idempotent: skips any feed whose url is already present. Run: uv run python scripts/opml_to_sources.py"""
from __future__ import annotations
import re, sys, xml.etree.ElementTree as ET
from pathlib import Path
import yaml

OPML = Path("references/hn-popular-blogs-2025.opml")
OUT = Path("config/sources.yaml")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "blog"


def main() -> int:
    existing = yaml.safe_load(OUT.read_text()) or []
    have = {e["url"] for e in existing}
    tree = ET.parse(OPML)
    appended = 0
    lines = []
    for o in tree.iter("outline"):
        url = o.get("xmlUrl")
        if not url or url in have:
            continue
        name = slug(o.get("title") or o.get("text") or url)
        lines.append(
            f'- {{name: hn-{name}, url: "{url}", type: blog, adapter: rss, '
            f"status: manual, priority: 5}}")
        have.add(url)
        appended += 1
    if lines:
        with OUT.open("a", encoding="utf-8") as f:
            f.write("\n# --- HN-popularity general blogs (manual, not run) ---\n")
            f.write("\n".join(lines) + "\n")
    print(f"appended {appended} manual sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
