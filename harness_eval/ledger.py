"""Append-only evaluation ledger (results/ledger.jsonl): every evaluation run, with exactly which code,
config, model weights and data produced it, and its headline metrics. Accesses to `test` / `release`
datasets are visible here, so repeated peeking at the frozen test set is auditable.

  python -m harness_eval.ledger show [--role test]
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.paths import ROOT

LEDGER = ROOT / "results" / "ledger.jsonl"


def record(command: str, cfg: Any, datasets: list[dict[str, Any]], headline: dict[str, Any],
           output_dir: Optional[str] = None, extra: Optional[dict[str, Any]] = None) -> str:
    from harness.phi import provenance
    run_id = f"run_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    row = {"run_id": run_id, "timestamp": datetime.now().isoformat(timespec="seconds"), "command": command,
           "operator": getpass.getuser(), "code_commit": provenance.code_commit(), "code_fingerprint": provenance.code_fingerprint(),
           "config_sha256": cfg.sha256(), "environment": cfg.raw.get("environment", "legacy"),
           "catalog_version": cfg.raw.get("catalog_version", "legacy"), "threshold_overlays": cfg.raw.get("_overlays", []),
           "models": {m: cfg.raw["models"][m].get("mode", "production") for m in cfg.enabled_models},
           "datasets": datasets, "test_access": any(d.get("role") in ("test", "release") for d in datasets),
           "headline": headline, "output_dir": output_dir, **(extra or {})}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return run_id


def read(role: Optional[str] = None) -> list[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    return [r for r in rows if role is None or any(d.get("role") == role for d in r.get("datasets", []))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show")
    s.add_argument("--role", default=None)
    a = ap.parse_args(argv)
    for r in read(a.role):
        print(f"{r['timestamp']}  {r['run_id']:<32} {r['command']:<12} cfg={r['config_sha256']} code={r['code_commit']:<18} "
              f"data={[d['id'] for d in r['datasets']]} headline={r['headline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
