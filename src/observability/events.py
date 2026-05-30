import json
import logging
from typing import Any


def emit(logger: logging.Logger, event: str, **params: Any) -> None:
    """Write one structured event as a JSON log line (runs-record stand-in)."""
    payload: dict[str, Any] = {"event": event, **params}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
