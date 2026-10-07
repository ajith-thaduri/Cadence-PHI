"""Release gates G1-G9: go / no-go (plan §15). The decision is the MINIMUM across gates.

  python -m harness_eval.gates run [--release-eval results/phi/<T4 run>] [--perf results/perf/<run>]
                                   [--with-model] [--output DIR]

Each gate reports PASS | FAIL | INSUFFICIENT_EVIDENCE with its reasons and the evidence it used. Any FAIL or
INSUFFICIENT_EVIDENCE -> NO-GO. Gate numbers come only from configs/eval/gates.yaml (status: proposed until the
risk owner signs them); this runner never adjusts them.

  G1-G3, G6  need a T4 evaluation: a scored run whose datasets are role test/release AND phi_class real_governed
             (double-annotated, adjudicated). Fabricated data (all data in this repository) cannot satisfy them.
  G4         prompt-suite regression vs reviewed baselines (fast tier always; model tier with --with-model)
  G5         CheckList MFT 100% for every enabled entity (fast tier always; model tier with --with-model)
  G7         fail-closed / security / boundary test files (pytest, subprocess)
  G8         performance run (harness_eval.perf all) evaluated against the gate's SLO
  G9         monitoring baseline, canary config (and a passing canary run), system card present
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.config import load_yaml
from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness_eval import ledger

GATES = ROOT / "configs" / "eval" / "gates.yaml"
PASS, FAIL, INSUFF = "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"


def _g(verdict: str, reasons: list[str], evidence: Any = None) -> dict[str, Any]:
    return {"verdict": verdict, "reasons": reasons, "evidence": evidence}


# ---------------------------------------------------------------------------- T4-backed gates
def _t4_run(path: Optional[str]) -> tuple[Optional[Path], list[str]]:
    if not path:
        return None, ["no T4 evaluation supplied (--release-eval): governed real-data evidence is required; "
                      "fabricated data cannot satisfy this gate"]
    run = Path(path)
    summ = json.loads((run / "summary.json").read_text())
    bad = [d for d, v in summ["datasets"].items()
           if (v.get("manifest") or {}).get("phi_class") != "real_governed" or (v.get("manifest") or {}).get("role") not in ("test", "release")]
    if bad:
        return None, [f"run {run} includes datasets that are not governed real test/release data: {bad}"]
    return run, []


def _entity_rows(run: Path) -> list[dict[str, str]]:
    with open(run / "entity_metrics.csv", newline="") as f:
        return list(csv.DictReader(f))


def gate_recall(run: Optional[Path], why: list[str], spec: dict, tier: str) -> dict[str, Any]:
    if run is None:
        return _g(INSUFF, why)
    rows = [r for r in _entity_rows(run) if r["risk_tier"] == tier and r["enabled"] == "True"]
    reasons, verdict = [], PASS
    for r in rows:
        lb_req = spec["min_lower_bound"]
        lb_req = lb_req.get(r["dataset"].split("_")[-1], min(lb_req.values())) if isinstance(lb_req, dict) else lb_req
        sup = int(r["support"] or 0)
        if sup < spec["min_support_mentions"]:
            verdict = INSUFF if verdict == PASS else verdict
            reasons.append(f"{r['dataset']}/{r['entity']}: support {sup} < {spec['min_support_mentions']}")
            continue
        lo = float(r["full_span_recall_ci_low"] or 0)
        if lo < lb_req:
            verdict = FAIL
            reasons.append(f"{r['dataset']}/{r['entity']}: recall CI lower bound {lo:.4f} < {lb_req}")
    return _g(verdict, reasons or ["all entities meet the lower bound"], str(run))


def gate_documents(run: Optional[Path], why: list[str], spec: dict) -> dict[str, Any]:
    if run is None:
        return _g(INSUFF, why)
    summ = json.loads((run / "summary.json").read_text())
    reasons, verdict = [], PASS
    for d, v in summ["datasets"].items():
        if v["documents"] < spec["min_documents"]:
            verdict = INSUFF if verdict == PASS else verdict
            reasons.append(f"{d}: {v['documents']} documents < {spec['min_documents']}")
            continue
        lo = v["ci95"]["direct_identifier_document_sanitization_rate"][0]
        if lo < spec["min_lower_bound"]:
            verdict = FAIL
            reasons.append(f"{d}: document sanitisation lower bound {lo:.4f} < {spec['min_lower_bound']}")
    return _g(verdict, reasons or ["met"], str(run))


def gate_utility(run: Optional[Path], why: list[str], spec: dict) -> dict[str, Any]:
    if run is None:
        return _g(INSUFF, why)
    summ = json.loads((run / "summary.json").read_text())
    reasons, verdict = [], PASS
    for d, v in summ["datasets"].items():
        if v["over_redaction_char_rate"] > spec["max_over_redaction_rate"]:
            verdict = FAIL
            reasons.append(f"{d}: over-redaction {v['over_redaction_char_rate']:.4f} > {spec['max_over_redaction_rate']}")
        if v["redaction_checks"]["must_not_redact_violations"] > spec["max_negative_suite_fp"]:
            verdict = FAIL
            reasons.append(f"{d}: must-not-redact violations {v['redaction_checks']['must_not_redact_violations']}")
    return _g(verdict, reasons or ["met"], str(run))


# ---------------------------------------------------------------------------- T1 / T2 gates
def _cfg(env: Optional[str], with_model: bool, model: bool) -> HarnessConfig:
    cfg = HarnessConfig.load(None, env=env)
    if not model:
        cfg.only_models([])
    return cfg


def gate_regression(env: Optional[str], with_model: bool, out: Path) -> dict[str, Any]:
    from harness_eval import suite
    s = ROOT / "eval_suites" / "english_v1"
    reasons, ev = [], {}
    tiers = [("fast", False, "baseline_fast.json")] + ([("model", True, "baseline.json")] if with_model else [])
    for name, model, base in tiers:
        run = out / f"suite_{name}"
        suite.run_suite(s, _cfg(env, with_model, model), "cpu", run)
        regs, imps = suite.regress(s, run, name=base)
        ev[name] = {"regressions": len(regs), "improvements": len(imps), "run": str(run)}
        reasons += [f"[{name}] {r}" for r in regs]
    verdict = FAIL if reasons else PASS
    if not with_model and verdict == PASS:
        verdict, reasons = INSUFF, ["model-tier regression not run (pass --with-model)"]
    return _g(verdict, reasons or ["no regressions"], ev)


def gate_behavioural(env: Optional[str], with_model: bool, out: Path) -> dict[str, Any]:
    from harness.phi.engine import PHIEngine
    from harness_eval import checklist
    ev, reasons = {}, []
    tiers = [("fast", False)] + ([("model", True)] if with_model else [])
    for name, model in tiers:
        cfg = _cfg(env, with_model, model)
        rows = checklist.run_cases(PHIEngine(cfg, device="cpu", self_test=False),
                                   checklist.generate({"fast"} if name == "fast" else {"model"}))
        s = checklist.summarize(rows, cfg)
        ev[name] = {"cases": s["cases"], "passed": s["passed"], "mft_pass_rate": s["mft_enabled_entities_pass_rate"]}
        if not s["gate_G5_mft_100pct"]:
            reasons.append(f"[{name}] MFT pass rate {s['mft_enabled_entities_pass_rate']}")
    verdict = FAIL if reasons else PASS
    if not with_model and verdict == PASS:
        verdict, reasons = INSUFF, ["model-tier CheckList not run (pass --with-model)"]
    return _g(verdict, reasons or ["MFT 100% for every enabled entity"], ev)


def gate_security(spec: dict) -> dict[str, Any]:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *spec["test_files"]],
                       cwd=ROOT, capture_output=True, text=True, timeout=1800)
    tail = (p.stdout.strip().splitlines() or ["no output"])[-1]
    return _g(PASS if p.returncode == 0 else FAIL, [tail], spec["test_files"])


def gate_performance(perf_dir: Optional[str]) -> dict[str, Any]:
    from harness_eval.perf import evaluate_g8
    cands = [Path(perf_dir)] if perf_dir else sorted((ROOT / "results" / "perf").glob("*/perf.json"), key=lambda p: p.stat().st_mtime)
    if perf_dir:
        cands = [Path(perf_dir) / "perf.json"]
    if not cands or not cands[-1].exists():
        return _g(INSUFF, ["no performance run (python -m harness_eval.perf all)"])
    res = json.loads(cands[-1].read_text())
    g = evaluate_g8(res, res.get("device", "cpu"))
    return _g(g["verdict"], g["reasons"] or ["within SLO"], str(cands[-1]))


def gate_operations(spec: dict, env: Optional[str]) -> dict[str, Any]:
    missing = [a for a in spec["required_artifacts"] if not (ROOT / a).exists()]
    reasons = [f"missing {m}" for m in missing]
    from harness.phi.engine import PHIEngine
    from harness.phi.monitoring import run_canaries
    rep = run_canaries(PHIEngine(HarnessConfig.load(None, env=env), device="cpu"))
    if not rep["ok"]:
        reasons.append(f"canaries failed: {rep['failed']}")
    return _g(PASS if not reasons else FAIL, reasons or ["artifacts present; canaries pass"], {"canaries": rep})


# ---------------------------------------------------------------------------- runner
def run(release_eval: Optional[str], perf_dir: Optional[str], with_model: bool, env: Optional[str], out: Path) -> dict[str, Any]:
    spec = load_yaml(str(GATES))
    g = spec["gates"]
    t4, why = _t4_run(release_eval)
    res = {
        "G1": gate_recall(t4, why, g["G1"], "direct"),
        "G2": gate_recall(t4, why, g["G2"], "quasi"),
        "G3": gate_documents(t4, why, g["G3"]),
        "G4": gate_regression(env, with_model, out),
        "G5": gate_behavioural(env, with_model, out),
        "G6": gate_utility(t4, why, g["G6"]),
        "G7": gate_security(g["G7"]),
        "G8": gate_performance(perf_dir),
        "G9": gate_operations(g["G9"], env),
    }
    order = {PASS: 0, INSUFF: 1, FAIL: 2}
    worst = max((v["verdict"] for v in res.values()), key=order.get)
    return {"created": datetime.now().isoformat(timespec="seconds"), "gates_version": spec["gates_version"],
            "gates_status": spec.get("status"), "decision": "GO" if worst == PASS else "NO-GO", "worst": worst, "gates": res}


def write(out: Path, r: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "gates.json").write_text(json.dumps(r, indent=1, default=str))
    L = [f"# Release gates - {r['created']}", "", f"**Decision: {r['decision']}** (gates v{r['gates_version']}, "
         f"status `{r['gates_status']}`; the decision is the minimum across gates)", "",
         "| gate | verdict | reasons |", "|---|---|---|"]
    names = load_yaml(str(GATES))["gates"]
    for k, v in r["gates"].items():
        L.append(f"| {k} {names[k]['name']} | **{v['verdict']}** | {'; '.join(v['reasons'])[:400]} |")
    (out / "gates.md").write_text("\n".join(L) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.gates")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--release-eval", default=None, help="scored T4 run dir (governed real test/release data)")
    r.add_argument("--perf", default=None, help="perf run dir (default: newest results/perf/*)")
    r.add_argument("--with-model", action="store_true", help="also run model-tier CheckList and suite regression")
    r.add_argument("--env", default=None)
    r.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    out = Path(a.output) if a.output else ROOT / "results" / "gates" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
    res = run(a.release_eval, a.perf, a.with_model, a.env, out)
    write(out, res)
    ledger.record("gates", HarnessConfig.load(None, env=a.env), [], {"decision": res["decision"],
                  **{k: v["verdict"] for k, v in res["gates"].items()}}, output_dir=str(out))
    print((out / "gates.md").read_text())
    return 0 if res["decision"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
