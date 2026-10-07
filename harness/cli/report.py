"""Generate the v2 HTML + Markdown report, CSVs and plots for an experiment directory.

  python -m harness.report [--run-dir results/<stamp>]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from harness.cli.common import setup_logging
from harness.evaluation.experiment import latest_run_dir
from harness.paths import REPORTS
from harness.taxonomy import HIGH_RISK


def _fn_examples(run_dir: Path, key: str, n: int = 12) -> list[dict]:
    p = run_dir / "false_negatives" / f"{key}__synthetic_test__full_shield.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: (r["entity_type"] not in HIGH_RISK, r["outcome"] != "miss", r["coverage"]))
    out = []
    for r in rows:
        if sum(1 for x in out if x["entity_type"] == r["entity_type"]) < 2:
            out.append(r)
        if len(out) >= n:
            break
    return out


def generate_report(run_dir: Path) -> dict[str, Path]:
    from harness.reporting.csv_export import write_csvs
    from harness.reporting.loader import load_run
    from harness.reporting.plots import make_all
    from harness.reporting.render import to_html, to_markdown
    from harness.reporting.sections import build_sections

    run = load_run(run_dir)
    run["_fn_examples"] = {k: _fn_examples(run_dir, k) for k in run["models"]}
    plots = make_all(run, run_dir / "plots")
    rep = run_dir / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    csvs = write_csvs(run, run_dir, rep)
    meta, secs = build_sections(run, {k: str(v) for k, v in plots.items()})
    rel = {str(v): f"plots/{Path(v).name}" for v in plots.values()}
    (rep / "final_report.md").write_text(to_markdown(meta, secs, {k: "../" + v for k, v in rel.items()}))
    (rep / "final_report.html").write_text(to_html(meta, secs))
    files = {"md": rep / "final_report.md", "html": rep / "final_report.html", **csvs}
    REPORTS.mkdir(exist_ok=True)
    for sub in ("plots", "false_negatives"):
        if (REPORTS / sub).exists():
            shutil.rmtree(REPORTS / sub)
        (REPORTS / sub).mkdir()
    for p in plots.values():
        shutil.copy2(p, REPORTS / "plots" / Path(p).name)
    for p in (run_dir / "false_negatives").glob("*.jsonl"):
        shutil.copy2(p, REPORTS / "false_negatives" / p.name)
    for p in files.values():
        shutil.copy2(p, REPORTS / Path(p).name)
    (REPORTS / "final_report.md").write_text(to_markdown(meta, secs, rel))
    (REPORTS / "SOURCE_RUN.txt").write_text(str(run_dir) + "\n")
    return files


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.report", description=__doc__)
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args(argv)
    setup_logging()
    rd = Path(args.run_dir) if args.run_dir else latest_run_dir()
    if rd is None:
        raise SystemExit("no experiment directory found")
    for k, p in generate_report(rd).items():
        print(f"{k:24s} {p}")
    print(f"copied to {REPORTS}/")


if __name__ == "__main__":
    main()
