from __future__ import annotations

import sys
from pathlib import Path

from src.pipeline.publish import flip_draft


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m src.tools.flip_draft <file.md>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.exists():
        return 0  # 缺文件不报错: 让 workflow 幂等容错
    path.write_text(flip_draft(path.read_text(encoding="utf-8")), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
