from __future__ import annotations

import os
from datetime import datetime


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _parse_dt(s: str) -> datetime:
    # GitHub ISO8601 ends in 'Z'; make it tz-aware
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
