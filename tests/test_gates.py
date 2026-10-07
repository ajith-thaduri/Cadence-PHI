"""P4 release gates: fabricated data can never satisfy the T4 gates; the decision is the minimum across gates;
G8 is judged only against configs/eval/gates.yaml."""
from __future__ import annotations

import csv
import json

from harness_eval import gates
from harness_eval.perf import evaluate_g8


def _run(tmp_path, phi_class, role="test", support=400, lo=0.995):
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.json").write_text(json.dumps({"datasets": {"cad_typed": {
        "manifest": {"phi_class": phi_class, "role": role}, "documents": 400,
        "ci95": {"direct_identifier_document_sanitization_rate": [0.995, 1.0]},
        "over_redaction_char_rate": 0.01, "redaction_checks": {"must_not_redact_violations": 0}}}}))
    with open(run / "entity_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["dataset", "entity", "enabled", "risk_tier", "support", "full_span_recall_ci_low"])
        w.writeheader()
        w.writerow({"dataset": "cad_typed", "entity": "PHONE", "enabled": "True", "risk_tier": "direct",
                    "support": support, "full_span_recall_ci_low": lo})
    return run


def test_fabricated_runs_cannot_satisfy_t4_gates(tmp_path):
    run, why = gates._t4_run(str(_run(tmp_path, "fabricated")))
    assert run is None and "not governed real" in why[0]
    assert gates.gate_recall(run, why, {"min_lower_bound": 0.99, "min_support_mentions": 300}, "direct")["verdict"] == gates.INSUFF
    assert gates._t4_run(None)[0] is None


def test_t4_recall_gate_pass_fail_and_support(tmp_path):
    spec = {"min_lower_bound": {"typed": 0.99}, "min_support_mentions": 300}
    ok, why = gates._t4_run(str(_run(tmp_path, "real_governed")))
    assert gates.gate_recall(ok, why, spec, "direct")["verdict"] == gates.PASS
    assert gates.gate_documents(ok, why, {"min_documents": 300, "min_lower_bound": 0.99})["verdict"] == gates.PASS
    (tmp_path / "run" / "entity_metrics.csv").unlink()
    (tmp_path / "run" / "summary.json").unlink()
    (tmp_path / "run").rmdir()
    low, why = gates._t4_run(str(_run(tmp_path, "real_governed", lo=0.97)))
    assert gates.gate_recall(low, why, spec, "direct")["verdict"] == gates.FAIL


def test_g8_requires_long_soak_and_slo():
    lat = {"<=4000": {"wall_ms": {"p95": 300, "p99": 600}, "failures": 0}}
    short = {"latency": lat, "soak": {"rss_growth_mb_per_hour": 1, "duration_s": 120}}
    assert evaluate_g8(short, "cpu")["verdict"] == gates.INSUFF
    long_ = {"latency": lat, "soak": {"rss_growth_mb_per_hour": 1, "duration_s": 3600}, "determinism": {"cpu_repeat_mismatched_docs": 0}}
    assert evaluate_g8(long_, "cpu")["verdict"] == gates.PASS
    slow = {**long_, "latency": {"<=4000": {"wall_ms": {"p95": 900, "p99": 1000}, "failures": 0}}}
    assert evaluate_g8(slow, "cpu")["verdict"] == gates.FAIL
