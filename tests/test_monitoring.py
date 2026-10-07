"""P5 monitoring hooks: canaries through the full engine path; Poisson control-chart alerts both ways;
canary traffic kept out of the detection-rate counters."""
from __future__ import annotations

from harness.phi import telemetry
from harness.phi.config import HarnessConfig
from harness.phi.engine import PHIEngine
from harness.phi.monitoring import check_rates, run_canaries
from test_phi_harness import SPANS, FakeAdapter, store


def _eng(cfg=None):
    cfg = cfg or HarnessConfig.load(env="test")
    return PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})


def test_configured_canaries_pass_and_stay_out_of_rate_counters():
    eng = _eng()
    before = {k: v for k, v in telemetry.snapshot().items() if k.startswith(("detections_total", "chars_total"))}
    rep = run_canaries(eng)
    after = {k: v for k, v in telemetry.snapshot().items() if k.startswith(("detections_total", "chars_total"))}
    assert rep["ok"] and rep["cases"] >= 4, rep
    assert before == after


def test_canary_detects_broken_detection():
    cfg = HarnessConfig.load(env="test")
    cfg.set_entities(["PHONE"], False)                       # simulate a bad config push
    eng = PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]}, self_test=False)
    rep = run_canaries(eng)
    assert not rep["ok"] and "typed_direct" in rep["failed"]


def test_rate_alerts_drop_and_surge():
    base = {"rates_per_1k_chars": {"telephone": {"PHONE": 2.0, "NAME": 5.0}}}
    snap = {"chars_total{modality=telephone}": 100_000,                      # expect 200 PHONE, 500 NAME
            "detections_total{entity=PHONE,modality=telephone}": 120,        # drop
            "detections_total{entity=NAME,modality=telephone}": 505}         # normal
    alerts = check_rates(base, snap, sigma=3, min_chars=20_000)
    assert [(a["entity"], a["direction"]) for a in alerts] == [("PHONE", "drop")]
    snap["detections_total{entity=NAME,modality=telephone}"] = 700
    assert ("NAME", "surge") in [(a["entity"], a["direction"]) for a in check_rates(base, snap)]
    assert check_rates(base, {"chars_total{modality=telephone}": 500}) == []  # too little traffic to judge
