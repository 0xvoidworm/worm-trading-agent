"""Unicode sparkline — tiny chart di satu baris, a la Bloomberg MINIF/GP.

Pakai block chars: ▁ ▂ ▃ ▄ ▅ ▆ ▇ █ (U+2581..U+2588).
"""
from __future__ import annotations

from typing import Iterable

BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: Iterable[float], width: int | None = None) -> str:
    vs = [float(v) for v in values if v == v]  # filter NaN
    if not vs:
        return ""
    if width and len(vs) > width:
        # downsample: ambil tail N
        vs = vs[-width:]
    lo = min(vs)
    hi = max(vs)
    span = hi - lo
    if span == 0:
        return BLOCKS[0] * len(vs)
    n = len(BLOCKS) - 1
    return "".join(BLOCKS[int((v - lo) / span * n)] for v in vs)
