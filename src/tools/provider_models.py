"""列出某个 provider 有哪些模型, 并逐个探活(只读)。

2026-09-19 实测: agnes 的限额**按模型算**——10:14:49 agnes-2.5-flash 成功,
两秒后 agnes-2.0-flash 连续 429。所以在 agnes 内部轮换多个模型能成倍提高免费额度下
的产量, 前提是知道有哪些模型可用。key 只在 GitHub Secrets 里, 所以跑在手动 workflow 上。
"""

from __future__ import annotations

import os
import sys

import httpx


def parse_models(data: dict) -> list[str]:
    return [m["id"] for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]


def probe_one(completions_url: str, api_key: str, model: str, timeout_s: float = 30.0):
    """(状态, 说明)。限流 ≠ 不存在: 被限的模型晚点还能用, 不该从名单里删。"""
    try:
        r = httpx.post(
            completions_url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with {}"}],
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout_s,
        )
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}: {e}"
    if r.status_code == 200:
        data = r.json()
        if not data.get("choices"):
            return "error", f"empty envelope, usage={data.get('usage')}"
        return "ok", ""
    if r.status_code == 429:
        return "rate_limited", r.text[:200]
    return "error", f"HTTP {r.status_code}: {r.text[:200]}"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    base = args[0] if args else "https://apihub.agnes-ai.com/v1"
    env = args[1] if len(args) > 1 else "AGNES_API_KEY"
    key = os.environ.get(env, "")
    if not key:
        print(f"missing {env}", file=sys.stderr)
        return 2
    r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30.0)
    r.raise_for_status()
    models = parse_models(r.json())
    print(f"{len(models)} models listed at {base}\n")
    for m in models:
        status, note = probe_one(f"{base}/chat/completions", key, m)
        print(f"{status:13} {m} {note}"[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
