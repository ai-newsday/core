from __future__ import annotations

import subprocess
from pathlib import Path

from src.core.types import WebsiteConfig


class WebsiteNotifier:
    def __init__(self, config: WebsiteConfig):
        self._cfg = config

    async def send_review_card(self, item_id: str, card: dict) -> None:
        return None

    async def send_final_report(self, markdown: str, summary: dict) -> None:
        if not self._cfg.enabled:
            return
        date_label = summary.get("date_label", "unknown")
        date_str = date_label.split("（")[0].strip()
        out_dir = Path(self._cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{date_str}.md"
        out_file.write_text(markdown, encoding="utf-8")
        if self._cfg.git_push:
            try:
                subprocess.run(["git", "add", str(out_file)], check=True, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", f"daily: {date_str}"], check=True, capture_output=True
                )
                subprocess.run(["git", "push"], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                pass

    async def poll_decisions(self) -> list[tuple[str, str]]:
        return []
