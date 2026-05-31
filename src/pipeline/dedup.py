from __future__ import annotations
import hashlib
import numpy as np
from src.core.types import RawItem


def build_embed_text(item: RawItem) -> str:
    """title_en + summary; degrade to title only when summary missing (spec §5.1)."""
    return f"{item.title_en}\n{item.raw_summary}" if item.raw_summary else item.title_en


def embedding_id(link: str) -> str:
    """Stable 16-hex id derived from the item link (spec §5.2)."""
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
