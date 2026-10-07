"""Small-sample statistics for de-identification evaluation.

  wilson(k, n)                 Wilson score interval for a proportion
  rule_of_three(n)             95% upper bound on the miss rate when 0 misses in n (Hanley & Lippman-Hand 1983)
  cluster_bootstrap(...)       percentile CI resampling whole DOCUMENTS (tokens within a document are correlated)
  paired_randomization(...)    approximate randomisation test for two systems on the same documents (i2b2 practice)
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

import numpy as np


def wilson(k: float, n: float, z: float = 1.959964) -> tuple[Optional[float], Optional[float]]:
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def rule_of_three(n: int) -> Optional[float]:
    """Upper 95% bound on the event rate when 0 events were observed in n trials."""
    return min(1.0, 3.0 / n) if n > 0 else None


def cluster_bootstrap(units: Sequence, stat: Callable[[Sequence], Optional[float]], replicates: int = 1000,
                      seed: int = 13, level: float = 0.95) -> tuple[Optional[float], Optional[float]]:
    """Percentile CI of stat(units) resampling the units (documents) with replacement."""
    n = len(units)
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(replicates):
        idx = rng.integers(0, n, n)
        v = stat([units[i] for i in idx])
        if v is not None:
            vals.append(v)
    if not vals:
        return None, None
    lo, hi = np.percentile(vals, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return float(lo), float(hi)


def paired_randomization(a: Sequence[float], b: Sequence[float], trials: int = 10000, seed: int = 13) -> float:
    """Two-sided p-value for mean(a) != mean(b) with per-document paired values (approximate randomisation)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) != len(b) or len(a) == 0:
        raise ValueError("paired samples must be non-empty and equal length")
    obs = abs(a.mean() - b.mean())
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(trials):
        swap = rng.random(len(a)) < 0.5
        aa, bb = np.where(swap, b, a), np.where(swap, a, b)
        if abs(aa.mean() - bb.mean()) >= obs - 1e-12:
            count += 1
    return (count + 1) / (trials + 1)
