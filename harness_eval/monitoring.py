"""Monitoring baseline (T8 / G9): expected detection rates per modality x entity, from DEVELOPMENT data only.

  python -m harness_eval.monitoring baseline [--dataset cadence_dev_text,cadence_dev_asr] [--model none]
  python -m harness_eval.monitoring canaries                # run the configured canaries once (exit 1 on failure)

The baseline is what harness.phi.monitoring.check_rates compares live traffic against. It is computed by the
same engine path production uses, stores counts and rates only (no text), and refuses non-dev datasets.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DecisionValue, DetectionOptions, Mode
from harness.phi.engine import PHIEngine
from harness.phi.monitoring import run_canaries
from harness_eval import datasets, ledger, manifests


def baseline(cfg: HarnessConfig, ds_ids: list[str], device: str) -> dict:
    manifests.require_role(ds_ids, {"dev"}, "monitoring baseline")
    det: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    chars: dict[str, int] = defaultdict(int)
    docs: dict[str, int] = defaultdict(int)
    for ds in ds_ids:
        reqs, _, store, _ = datasets.load(ds, languages=["en"])
        eng = PHIEngine(cfg, device=device, registry_store=store)
        for r in reqs:
            resp = eng.redact(r.model_copy(update={"options": DetectionOptions(mode=Mode.REDACT)}))
            mod = r.modality if isinstance(r.modality, str) else r.modality.value
            chars[mod] += len(r.text)
            docs[mod] += 1
            for d in resp.detections:
                if d.decision != DecisionValue.KEEP:
                    det[mod][d.entity_type] += 1
    rates = {m: {e: round(n * 1000 / chars[m], 4) for e, n in sorted(es.items())} for m, es in det.items()}
    return {"created": datetime.now().isoformat(timespec="seconds"), "config_sha256": cfg.sha256(), "datasets": ds_ids,
            "documents": dict(docs), "chars": dict(chars), "detections": {m: dict(v) for m, v in det.items()},
            "rates_per_1k_chars": rates,
            "note": "development data only; recompute on representative governed traffic before go-live"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.monitoring")
    ap.add_argument("cmd", choices=["baseline", "canaries"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--env", default=None)
    ap.add_argument("--dataset", default="cadence_dev_text,cadence_dev_asr")
    ap.add_argument("--model", default=None)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    cfg = HarnessConfig.load(a.config, env=a.env)
    if a.model:
        cfg.only_models([] if a.model == "none" else a.model.split(","))
    if a.cmd == "canaries":
        rep = run_canaries(PHIEngine(cfg, device=a.device))
        print(json.dumps(rep, indent=1))
        return 0 if rep["ok"] else 1
    ids = [x.strip() for x in a.dataset.split(",") if x.strip()]
    b = baseline(cfg, ids, a.device)
    out = Path(a.output) if a.output else ROOT / (cfg.raw.get("monitoring") or {}).get("detection_rate", {}).get(
        "baseline", "results/monitoring/baseline.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(b, indent=1, sort_keys=True))
    ledger.record("monitoring_baseline", cfg, [{"id": i, "role": "dev"} for i in ids], {"output": str(out)})
    print(f"baseline: {sum(b['documents'].values())} documents, modalities {sorted(b['rates_per_1k_chars'])} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
