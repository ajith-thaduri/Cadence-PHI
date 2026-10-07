"""Latency / memory benchmark.

  python -m harness.benchmark --all-models
  python -m harness.benchmark --model stanford_base --device cpu
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.cli.common import setup_logging
from harness.config import all_model_keys, benchmark_config, default_model_keys
from harness.datasets import load_dataset
from harness.devices import detect_device, hardware_info
from harness.evaluation.experiment import latest_run_dir, new_run_dir


def run_benchmark(models: list[str], device: str = "auto", run_dir=None, iters=None, warmup=None,
                  cold: bool = True, resume: bool = False, tag: str | None = None, isolate: bool = False) -> Path:
    from harness.benchmarking.latency import benchmark_model

    dev = detect_device(device)
    rd = Path(run_dir) if run_dir else (latest_run_dir() or new_run_dir())
    (rd / "latency").mkdir(parents=True, exist_ok=True)
    ds = load_dataset("synthetic_test")
    # Fixed, representative workload: non-long documents (Cadence-sized turns/notes).
    short = [s.text for s in ds.samples if s.subset not in ("LONG_DOCUMENTS", "NEGATIVE_CONTROL")]
    texts = short[:40]
    batch = short[:benchmark_config()["benchmark"]["batch_size"]]
    for key in models:
        out = rd / "latency" / f"{key}{'__' + tag if tag else ''}.json"
        if resume and out.exists():
            continue
        print(f"benchmarking {key} on {dev} ...", flush=True)
        if isolate:
            import subprocess
            import sys
            cmd = [sys.executable, "-m", "harness.benchmark", "--model", key, "--device", dev, "--run-dir", str(rd)]
            cmd += (["--tag", tag] if tag else []) + ([] if cold and tag is None else ["--no-cold-start"])
            subprocess.run(cmd, check=False)
            continue
        r = benchmark_model(key, dev, texts, batch, warmup=warmup, iters=iters, do_cold=cold and tag is None)
        r["hardware"] = hardware_info()
        out.write_text(json.dumps(r, indent=2, default=str))
        if "single_doc" in r:
            print(f"  median {r['single_doc']['median_ms']:.1f} ms  p95 {r['single_doc']['p95_ms']:.1f} ms  "
                  f"load {r['load_time_s']:.1f} s")
        else:
            print(f"  {r['status']}: {r.get('error')}")
    return rd


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.benchmark", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", choices=all_model_keys())
    g.add_argument("--all-models", action="store_true")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--run-dir", default=None, help="default: latest experiment directory")
    ap.add_argument("--iterations", type=int, default=None, help="measured iterations (min 20)")
    ap.add_argument("--warmup", type=int, default=None, help="warm-up iterations (min 3)")
    ap.add_argument("--no-cold-start", action="store_true")
    ap.add_argument("--tag", default=None, help="suffix for the latency file, e.g. cpu")
    args = ap.parse_args(argv)
    setup_logging()
    rd = run_benchmark(default_model_keys() if args.all_models else [args.model], args.device, args.run_dir,
                       args.iterations, args.warmup, not args.no_cold_start, tag=args.tag)
    print(f"latency results: {rd / 'latency'}")


if __name__ == "__main__":
    main()
