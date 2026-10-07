"""P5 shadow mode: a source in mode: shadow runs and its evidence is recorded, but it never changes a decision
or the sanitized output; what it WOULD have added is counted (shadow_only_spans) for monitoring."""
from __future__ import annotations

from harness.phi.config import HarnessConfig
from harness.phi.contracts import RejectionReason
from harness.phi.engine import PHIEngine
from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store


def _run(cfg):
    # self_test off: switching a sentinel-critical source to shadow/off correctly refuses startup (tested elsewhere)
    eng = PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]}, self_test=False)
    return eng.redact(request(TEXT, options={"mode": "debug", "include_candidates": True}))


def test_shadow_model_never_changes_output():
    base = HarnessConfig.load(env="test")
    m = next(iter(base.enabled_models))
    sh = base.copy()
    sh.set_value(f"models.{m}.mode", "shadow")
    sh.set_value(f"thresholds.model.{m}", 0.4)           # so the fixture's model-only span ('Lucia', 0.42) would pass
    off = base.copy()
    off.only_models([])
    r_shadow, r_off = _run(sh), _run(off)
    assert r_shadow.output.sanitized_text == r_off.output.sanitized_text
    assert [(d.original_start, d.original_end, d.entity_type) for d in r_shadow.detections] == \
           [(d.original_start, d.original_end, d.entity_type) for d in r_off.detections]
    ev = [e for e in r_shadow.evidence if e.recognizer_id == f"model_{m}"]
    assert ev and all(e.rejection_reason == RejectionReason.shadow_mode and not e.accepted_as_candidate for e in ev)
    assert all("shadow_would_accept" in e.metadata for e in ev)
    assert r_shadow.summary.shadow_only_spans >= 1           # the fake model finds names the rules do not


def test_shadow_recognizer_is_inert():
    base = HarnessConfig.load(env="test")
    rid = next(r for r, s in base.raw["recognizers"].items() if s.get("enabled") and s.get("family"))
    sh = base.copy()
    sh.set_value(f"recognizers.{rid}.mode", "shadow")
    off = base.copy()
    off.set_recognizers([rid], False)
    assert _run(sh).output.sanitized_text == _run(off).output.sanitized_text
