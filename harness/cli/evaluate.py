"""Evaluate models across all evaluation pairs and PHI Shield stages.

  python -m harness.evaluate --all-models
  python -m harness.evaluate --model obi_roberta
  python -m harness.evaluate --model stanford_base --datasets synthetic
  python -m harness.evaluate --all-models --dataset i2b2_2014 --data-path /path --allow-local-public-dataset
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from harness.cli.common import setup_logging
from harness.config import all_model_keys, default_model_keys
from harness.datasets import DatasetUnavailable, load_dataset
from harness.devices import detect_device
from harness.evaluation.experiment import new_run_dir, write_run_info
from harness.evaluation.runner import EVAL_PAIRS, evaluate_model, evaluate_shield_baselines
from harness.privacy import PUBLIC_DATA_WARNING
from harness.shield.pipeline import ShieldComponents

log = logging.getLogger("harness.evaluate")
RESTRICTED = ["i2b2_2014", "i2b2_2006", "physionet_deid"]


def load_all(groups: list[str], extra: str | None = None, data_path=None, allow_public=False) -> tuple[dict, dict]:
    names = []
    if "synthetic" in groups:
        names += ["synthetic_dev", "synthetic_test"]
    if "meddocan" in groups:
        names += ["meddocan_dev", "meddocan_test"]
    datasets, status = {}, {}
    for n in names:
        try:
            datasets[n] = load_dataset(n)
            status[n] = {"status": "AVAILABLE", **datasets[n].describe()}
        except DatasetUnavailable as exc:
            status[n] = {"status": "NOT RUN — DATASET UNAVAILABLE", "reason": str(exc)}
    for n in RESTRICTED + ([extra] if extra and extra not in RESTRICTED else []):
        try:
            d = load_dataset(n, data_path if n == extra else None, allow_public)
            print(PUBLIC_DATA_WARNING)
            datasets[n] = d
            status[n] = {"status": "AVAILABLE", **d.describe()}
        except (DatasetUnavailable, KeyError) as exc:
            status[n] = {"status": "NOT RUN — DATASET UNAVAILABLE", "reason": str(exc)}
    return datasets, status


def run_evaluation(models: list[str], groups: list[str], device="auto", run_dir=None, extra=None, data_path=None,
                   allow_public=False, resume=False, isolate=False) -> tuple[Path, list[dict[str, Any]]]:
    dev = detect_device(device)
    rd = Path(run_dir) if run_dir else new_run_dir()
    for sub in ("metrics", "models", "raw", "false_negatives", "false_positives", "predictions"):
        (rd / sub).mkdir(parents=True, exist_ok=True)
    datasets, status = load_all(groups, extra, data_path, allow_public)
    (rd / "metrics" / "_datasets.json").write_text(json.dumps(status, indent=1, default=str))
    if not (rd / "config.json").exists():
        write_run_info(rd, {"models": models, "groups": groups, "device": device}, dev, status)
    shield = {n: ShieldComponents() for n in datasets}
    if isolate and len(models) > 1:
        pass   # baselines below; models run in subprocesses (each loads datasets itself)
    if not (resume and (rd / "metrics" / "_shield_baselines.json").exists()):
        evaluate_shield_baselines(datasets, shield, rd)
    results = []
    for key in models:
        if isolate:
            # one subprocess per model: frees all model / accelerator memory between models
            import subprocess
            import sys
            if resume and (rd / "metrics" / f"{key}.json").exists() and \
                    json.loads((rd / "metrics" / f"{key}.json").read_text()).get("status") == "PASS":
                log.info("[%s] already evaluated - skipping (resume)", key)
            else:
                cmd = [sys.executable, "-m", "harness.evaluate", "--model", key, "--run-dir", str(rd), "--resume",
                       "--datasets", ",".join(groups), "--device", device, "--no-summary"]
                log.info("[%s] subprocess: %s", key, " ".join(cmd[1:]))
                subprocess.run(cmd, check=False)
            p = rd / "metrics" / f"{key}.json"
            results.append(json.loads(p.read_text()) if p.exists() else {"model": key, "status": "FAILED (subprocess)"})
            continue
        if resume and (rd / "metrics" / f"{key}.json").exists():
            r = json.loads((rd / "metrics" / f"{key}.json").read_text())
            if r.get("status") == "PASS":
                log.info("[%s] already evaluated - skipping (resume)", key)
                results.append(r)
                continue
        results.append(evaluate_model(key, dev, datasets, shield, rd))
        log.info("[%s] %s", key, results[-1]["status"])
    return rd, results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.evaluate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", choices=all_model_keys())
    g.add_argument("--all-models", action="store_true", help="all models except provenance-excluded ones")
    ap.add_argument("--datasets", default="synthetic,meddocan", help="comma list: synthetic, meddocan")
    ap.add_argument("--dataset", default=None, help="additional restricted dataset (i2b2_2014 | i2b2_2006 | physionet_deid)")
    ap.add_argument("--data-path", default=None)
    ap.add_argument("--allow-local-public-dataset", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--isolate", action="store_true", help="evaluate each model in its own subprocess")
    ap.add_argument("--no-summary", action="store_true")
    args = ap.parse_args(argv)
    setup_logging(args.debug)
    models = default_model_keys() if args.all_models else [args.model]
    rd, _ = run_evaluation(models, args.datasets.split(","), args.device, args.run_dir, args.dataset,
                           args.data_path, args.allow_local_public_dataset, args.resume, args.isolate)
    if args.no_summary:
        return
    from harness.reporting.summary import print_terminal_summary
    print_terminal_summary(rd)
    print(f"\nexperiment directory: {rd}")


if __name__ == "__main__":
    main()
