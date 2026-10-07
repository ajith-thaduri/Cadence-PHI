"""python -m harness_eval.export --dataset cadence_test [--out data/requests]

Writes the canonical interchange files for a benchmark dataset: <ds>.requests.jsonl
(DetectionRequest per line - no gold), <ds>.gold.jsonl (GoldDocument per line) and the scoped
registry store (registry/subjects/<ctx>.json). Fabricated / public data only; local files."""
from __future__ import annotations

import argparse
from pathlib import Path

from harness_eval.datasets import expand, export


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.export")
    ap.add_argument("--dataset", action="append", required=True)
    ap.add_argument("--out", default="data/requests")
    a = ap.parse_args(argv)
    from harness.phi.config import HarnessConfig
    langs = HarnessConfig.load().supported_languages
    for n in expand(sum((d.split(",") for d in a.dataset), [])):
        p = export(n, Path(a.out), languages=langs)
        print(n, {k: str(v) for k, v in p.items()})


if __name__ == "__main__":
    main()
