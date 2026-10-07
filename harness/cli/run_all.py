"""End-to-end pipeline: synthetic data -> all models x (synthetic, MEDDOCAN) x shield stages
-> long-doc / boundary / streaming tests -> latency (MPS + CPU) -> report.

  python -m harness.run_all [--device auto] [--skip-benchmark] [--resume --run-dir results/...]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from harness.cli.common import setup_logging
from harness.config import default_model_keys

log = logging.getLogger("harness.run_all")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.run_all", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"])
    ap.add_argument("--skip-benchmark", action="store_true")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args(argv)
    setup_logging()
    from harness.cli.benchmark import run_benchmark
    from harness.cli.evaluate import run_evaluation
    from harness.cli.report import generate_report
    from harness.datagen.generate import generate_all
    from harness.paths import SYNTHETIC
    from harness.reporting.summary import print_terminal_summary

    if not args.resume:
        log.info("generating synthetic benchmark v2")
        generate_all(SYNTHETIC)
    rd, _ = run_evaluation(default_model_keys(), ["synthetic", "meddocan"], args.device, args.run_dir,
                           resume=args.resume, isolate=True)
    if not args.skip_benchmark:
        run_benchmark(default_model_keys(), args.device, run_dir=str(rd), resume=True, isolate=True)
        run_benchmark(default_model_keys(), "cpu", run_dir=str(rd), resume=True, tag="cpu", isolate=True)
    files = generate_report(Path(rd))
    print_terminal_summary(Path(rd))
    print(f"\nHTML report: {files['html']}\nMarkdown:    {files['md']}\nexperiment:  {rd}")


if __name__ == "__main__":
    main()
