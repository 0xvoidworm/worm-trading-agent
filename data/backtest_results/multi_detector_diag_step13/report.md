# Multi-Detector Predictive Diagnostic — Step 13A + 13B

_Generated: 2026-04-26T09:27:38.080927_

Read-only. Predictive test: untuk tiap signal, signed forward return (direction-aligned) di 1h, 4h, 24h. Hit-rate = % return > 0 setelah sign-correction.

## Section A — 13A REGIME Inversion Test

Original direction: `to=trending_up`→LONG, `to=trending_down`→SHORT.
Inverted: flip both.

### Original direction

| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |
|---|---|---|---|
| `[0,1)` | — | — | — |
| `[1,2)` | — | — | — |
| `[2,3)` | 1731 / 0.467 / +0.017% | 1731 / 0.468 / +0.022% | 1725 / 0.510 / +0.141% |
| `[3,5)` | 335 / 0.409 / -0.065% | 335 / 0.475 / -0.129% | 331 / 0.462 / -0.375% |
| `[5,8)` | 368 / 0.476 / -0.042% | 368 / 0.413 / -0.140% | 366 / 0.432 / +0.019% |
| `[8,12)` | 389 / 0.496 / +0.022% | 389 / 0.478 / -0.124% | 388 / 0.433 / -0.236% |
| `[12,20)` | 212 / 0.505 / -0.022% | 212 / 0.500 / +0.114% | 212 / 0.509 / +0.563% |
| `[20,+)` | 6 / 0.500 / +0.109% | 6 / 0.500 / +0.481% | 6 / 0.167 / -2.140% |

### Inverted direction

| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |
|---|---|---|---|
| `[0,1)` | — | — | — |
| `[1,2)` | — | — | — |
| `[2,3)` | 1731 / 0.518 / -0.017% | 1731 / 0.523 / -0.022% | 1725 / 0.489 / -0.141% |
| `[3,5)` | 335 / 0.558 / +0.065% | 335 / 0.516 / +0.129% | 331 / 0.535 / +0.375% |
| `[5,8)` | 368 / 0.495 / +0.042% | 368 / 0.584 / +0.140% | 366 / 0.566 / -0.019% |
| `[8,12)` | 389 / 0.496 / -0.022% | 389 / 0.512 / +0.124% | 388 / 0.564 / +0.236% |
| `[12,20)` | 212 / 0.486 / +0.022% | 212 / 0.495 / -0.114% | 212 / 0.491 / -0.563% |
| `[20,+)` | 6 / 0.500 / -0.109% | 6 / 0.500 / -0.481% | 6 / 0.833 / +2.140% |

### 13A Verdict

- Forward overall hit-rate: h=1→0.468, h=4→0.466, h=24→0.484
- Forward top-vs-bot uplift: h=1→+0.037, h=4→+0.032, h=24→-0.000
- Inverted overall hit-rate: h=1→0.514, h=4→0.526, h=24→0.514
- Inverted top-vs-bot uplift: h=1→-0.032, h=4→-0.028, h=24→+0.001

→ **INVERSION_NEUTRAL** (S3 confirmed: no edge in either direction)

## Section B — 13B Multi-Detector Predictive Power

### FUNDING_EXTREME

_side=long-pays→SHORT, side=short-pays→LONG. Strength = |rate|._

| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |
|---|---|---|---|
| `[0,0.01%)` | — | — | — |
| `[0.01,0.03%)` | 31 / 0.484 / +0.073% | 31 / 0.516 / -0.009% | 31 / 0.387 / -0.528% |
| `[0.03,0.05%)` | 41 / 0.415 / -0.019% | 41 / 0.488 / -0.173% | 41 / 0.463 / -0.376% |
| `[0.05,0.10%)` | 5 / 0.800 / +0.366% | 5 / 0.600 / +0.768% | 5 / 0.600 / +3.054% |
| `[0.10,0.30%)` | 7 / 0.429 / -1.086% | 7 / 0.000 / -2.119% | 7 / 0.286 / -2.702% |
| `[0.30%+)` | 4 / 0.000 / -2.259% | 4 / 0.750 / +2.499% | 4 / 0.000 / -2.869% |

- Overall hit-rate: h=1→0.443, h=4→0.477, h=24→0.409
- Top-vs-bot uplift: h=1→-0.069, h=4→-0.028, h=24→+0.076
- **Verdict: HAS_EDGE** (forward hit-rate or uplift exceeds threshold)

### FUNDING_FLIP

_curr>0→LONG (bullish flip), curr<0→SHORT (bearish flip). Strength = |curr|._

| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |
|---|---|---|---|
| `[0,0.01%)` | — | — | — |
| `[0.01,0.03%)` | 28 / 0.714 / +0.196% | 28 / 0.679 / +0.438% | 28 / 0.607 / +1.085% |
| `[0.03,0.05%)` | 7 / 0.429 / +0.154% | 7 / 0.857 / +0.501% | 7 / 0.429 / -0.563% |
| `[0.05,0.10%)` | — | — | — |
| `[0.10,0.30%)` | 2 / 1.000 / +3.585% | 2 / 1.000 / +1.055% | 2 / 1.000 / +6.577% |
| `[0.30%+)` | 4 / 1.000 / +2.259% | 4 / 0.250 / -2.499% | 4 / 1.000 / +2.869% |

- Overall hit-rate: h=1→0.707, h=4→0.683, h=24→0.634
- Top-vs-bot uplift: 
- **Verdict: HAS_EDGE** (forward hit-rate or uplift exceeds threshold)

### Z_SPIKE

_side=short-spread→SHORT A, side=long-spread→LONG A. Strength = |z|. Predictive on first-symbol price._

| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |
|---|---|---|---|
| `[2.0,2.5)` | 149 / 0.430 / +0.059% | 149 / 0.470 / +0.003% | 149 / 0.443 / -0.136% |
| `[2.5,3.0)` | 34 / 0.559 / +0.238% | 34 / 0.471 / +0.074% | 34 / 0.382 / +0.010% |
| `[3.0,4.0)` | 8 / 0.625 / +0.177% | 8 / 0.625 / +0.237% | 8 / 0.750 / +1.264% |
| `[4.0,6.0)` | 3 / 0.333 / -1.036% | 3 / 0.667 / -0.248% | 3 / 0.667 / -1.253% |
| `[6.0,+)` | — | — | — |

- Overall hit-rate: h=1→0.459, h=4→0.479, h=24→0.448
- Top-vs-bot uplift: h=1→+0.129, h=4→+0.001, h=24→-0.061
- **Verdict: HAS_EDGE** (forward hit-rate or uplift exceeds threshold)

## Section B Summary

| Detector | Verdict | Best uplift | Best hit-rate |
|---|---|---|---|
| REGIME_CHANGE (forward) | FLAT | +0.037 | 0.484 |
| REGIME_CHANGE (inverted) | — | +0.001 | 0.526 |
| FUNDING_EXTREME | HAS_EDGE | +0.076 | 0.477 |
| FUNDING_FLIP | HAS_EDGE | +nan | 0.707 |
| Z_SPIKE | HAS_EDGE | +0.129 | 0.479 |
