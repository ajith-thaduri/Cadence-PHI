"""Test-set contamination checks: exact and near-duplicate documents across dataset roles.

  python -m harness_eval.contamination [--threshold 0.5]

Near-duplicates are measured with word 5-gram shingle Jaccard similarity (exact, no sampling: the
corpora are small). Pairs above the threshold between a `dev` dataset and any `test` / `suite` /
`release` dataset are contamination: dev-derived rules or thresholds could have seen the test text.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import product
from pathlib import Path
from typing import Any

from harness.paths import ROOT
from harness_eval import manifests

CHECK_PAIRS = [("dev", "test"), ("dev", "suite"), ("dev", "release"), ("suite", "test")]


def shingles(text: str, n: int = 5) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(toks[i:i + n]) for i in range(max(1, len(toks) - n + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _texts(ds_id: str) -> dict[str, str]:
    e = manifests.entry(ds_id)
    p = ROOT / e["path"]
    out = {}
    if not p.exists():
        return out
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            r = json.loads(line)
            out[str(r.get("id") or r.get("prompt_id") or i)] = r.get("text", "")
    return out


def check(threshold: float = 0.5) -> list[dict[str, Any]]:
    m = manifests.load()
    by_role: dict[str, list[str]] = {}
    for ds, e in (m.get("datasets") or {}).items():
        by_role.setdefault(e["role"], []).append(ds)
    cache = {ds: {k: shingles(t) for k, t in _texts(ds).items()} for ds in m.get("datasets") or {}}
    hits = []
    for ra, rb in CHECK_PAIRS:
        for da, db in product(by_role.get(ra, []), by_role.get(rb, [])):
            if da == db:
                continue
            for (ka, sa), (kb, sb) in product(cache[da].items(), cache[db].items()):
                j = jaccard(sa, sb)
                if j >= threshold:
                    hits.append({"a": f"{da}:{ka}", "b": f"{db}:{kb}", "jaccard": round(j, 3), "roles": f"{ra}->{rb}"})
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.contamination")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args(argv)
    hits = check(a.threshold)
    for h in hits[:50]:
        print(f"NEAR-DUPLICATE {h['roles']:<12} {h['a']} ~ {h['b']}  jaccard={h['jaccard']}")
    print(f"{len(hits)} contaminated pair(s) at threshold {a.threshold}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
