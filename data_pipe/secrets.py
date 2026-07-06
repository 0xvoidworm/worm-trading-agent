"""Secrets loader — baca .env di project root, cache di memori.

Pattern sengaja minimal: tidak pakai python-dotenv untuk hindari dependency tambahan.
Simple parser yang handle: KEY=value, comment, blank line, quoted value.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


@lru_cache(maxsize=1)
def _load() -> dict:
    """Parse .env satu kali, cache hasilnya."""
    if not ENV_PATH.exists():
        return {}
    result = {}
    for raw_line in ENV_PATH.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            result[key] = val
    return result


def get(key: str, default: str | None = None) -> str | None:
    """Ambil value: .env dulu, fallback ke os.environ, fallback ke default.

    Use explicit key presence instead of `or` so intentional values like "0"
    or an empty override are not silently replaced by another source.
    """
    env = _load()
    if key in env:
        return env[key]
    if key in os.environ:
        return os.environ[key]
    return default


def require(key: str) -> str:
    """Ambil value, raise kalau kosong."""
    v = get(key)
    if not v:
        raise RuntimeError(
            f"Missing required secret: {key}. "
            f"Set it in {ENV_PATH} (copy dari .env.example kalau belum ada)."
        )
    return v
