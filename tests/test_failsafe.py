"""T5 fault injection: every failure point must fail CLOSED (no detections, no sanitized text, no PHI,
status "blocked" - or "fallback" where the policy explicitly allows a conservative fallback).
NIST SP 800-53 SC-24 / SI-17 / SI-11; NIST SP 800-188 5.3 containment."""
from __future__ import annotations

import json
import time

import pytest

from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode, Operator, RedactionRequest
from harness.phi.engine import PHIEngine
from harness.phi.failsafe import EngineStartupError
from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store

SECRETS = ("Maria", "Hernandez", "Lucia", "Pierce", "nine one seven")


class ExplodingAdapter(FakeAdapter):
    def infer_raw(self, text, thr=None):
        if not text.startswith("SELFTEST"):
            raise RuntimeError(f"CUDA error while processing: {text}")     # exception text contains PHI on purpose
        return super().infer_raw(text, thr)


class SlowAdapter(FakeAdapter):
    def infer_raw(self, text, thr=None):
        if not text.startswith("SELFTEST"):
            time.sleep(0.5)
        return super().infer_raw(text, thr)


def make(cfg=None, adapter_cls=FakeAdapter, **kw):
    cfg = cfg or HarnessConfig.load()
    return PHIEngine(cfg, registry_store=store(), adapters={m: adapter_cls(m, SPANS) for m in cfg.raw["models"]}, **kw)


def assert_blocked(resp, failure_class):
    assert resp.status == "blocked", resp.status
    assert resp.failure.failure_class == failure_class
    assert resp.detections == [] and resp.evidence == [] and resp.output.sanitized_text is None
    blob = resp.model_dump_json()
    for s in SECRETS:
        assert s not in blob                                                # SI-11: no PHI / exception text
    assert "CUDA" not in blob and "Traceback" not in blob
    assert resp.stamps is not None and resp.stamps.config_sha256


def test_model_exception_blocks_without_leaking_exception_text():
    resp = make(adapter_cls=ExplodingAdapter).redact(request(options={"mode": "redact"}))
    assert_blocked(resp, "model_error")


def test_model_timeout_blocks():
    c = HarnessConfig.load()
    c.raw["failure"]["deadline_ms"] = {"total": 5000, "model": 50}
    resp = make(c, adapter_cls=SlowAdapter).analyze(request())
    assert_blocked(resp, "timeout")


def test_total_deadline_blocks():
    c = HarnessConfig.load()
    c.raw["failure"]["deadline_ms"] = {"total": 100, "model": 5000}
    resp = make(c, adapter_cls=SlowAdapter, self_test=False).analyze(request())
    assert_blocked(resp, "timeout")


def test_conservative_fallback_uses_rules_and_registry_only():
    c = HarnessConfig.load()
    c.raw["failure"]["on_model_error"] = "conservative_fallback"
    resp = make(c, adapter_cls=ExplodingAdapter).redact(request(options={"mode": "redact"}))
    assert resp.status == "fallback" and resp.failure.failure_class == "model_error"
    assert resp.summary.models_used == []
    assert "[PHONE_1]" in resp.output.sanitized_text                        # rules still protect
    assert "Maria Hernandez" not in resp.output.sanitized_text              # registry still protects


def test_oversize_input_rejected_never_truncated():
    c = HarnessConfig.load()
    c.raw["failure"]["max_input_chars"] = 100
    resp = make(c).redact(request(options={"mode": "redact"}))
    assert_blocked(resp, "oversize_input")
    assert resp.failure.action == "reject"


def test_unknown_registry_subject_blocks():
    resp = make().analyze(request(registry_context={"subject_context_id": "ctx_does_not_exist"}))
    assert_blocked(resp, "registry_unavailable")


def test_recognizer_exception_blocks(monkeypatch):
    from harness.phi.recognizers.rules import RuleFamilyRecognizer
    eng = make()

    def boom(self, text):
        raise ValueError(f"bad regex state near {text[:20]}")
    monkeypatch.setattr(RuleFamilyRecognizer, "hits", boom)
    assert_blocked(eng.analyze(request()), "recognizer_error")


def test_internal_error_blocks(monkeypatch):
    import harness.phi.engine as E
    eng = make()
    monkeypatch.setattr(E, "decide", lambda *a, **k: (_ for _ in ()).throw(KeyError("Maria Hernandez")))
    assert_blocked(eng.analyze(request()), "internal_error")


def test_missing_pseudonym_or_encryption_key_fails_closed(monkeypatch):
    monkeypatch.delenv("PHI_PSEUDONYM_KEY", raising=False)
    monkeypatch.delenv("PHI_ENCRYPTION_KEY", raising=False)
    eng = make()
    resp = eng.analyze(request(options={"mode": "detect"}))
    rr = eng.redact(RedactionRequest(request_id="req_k", original_text=TEXT, detections=resp.detections,
                                     operator=Operator.PSEUDONYMIZE))
    assert rr.status == "blocked" and rr.failure.failure_class == "key_unavailable" and rr.sanitized_text == ""
    resp2 = eng.redact(request(options=DetectionOptions(mode=Mode.REDACT, redaction_operator="ENCRYPT")))
    assert_blocked(resp2, "key_unavailable")


def test_pseudonyms_stable_with_managed_key(monkeypatch):
    monkeypatch.setenv("PHI_PSEUDONYM_KEY", "k" * 32)
    eng = make()
    d = eng.analyze(request(options={"mode": "detect"})).detections
    a = eng.redact(RedactionRequest(request_id="r1", original_text=TEXT, detections=d, operator=Operator.PSEUDONYMIZE))
    b = eng.redact(RedactionRequest(request_id="r2", original_text=TEXT, detections=d, operator=Operator.PSEUDONYMIZE))
    assert a.status == "ok" and a.sanitized_text == b.sanitized_text and "Maria" not in a.sanitized_text


def test_invalid_failure_policy_refuses_to_start():
    c = HarnessConfig.load()
    c.raw["failure"]["on_model_error"] = "pass_through"
    with pytest.raises(EngineStartupError):
        make(c)


def test_failed_self_test_refuses_to_start():
    c = HarnessConfig.load()
    c.set_recognizers(["rule_phone", "rule_spoken_number", "conv_spoken_id", "conv_partial_phone", "rule_identifier"], False)
    with pytest.raises(EngineStartupError):
        make(c)


def test_weight_hash_mismatch_refuses_to_start(monkeypatch):
    """Weights the engine loads itself are verified against the pin; caller-injected test doubles are not."""
    import harness.phi.engine as E
    monkeypatch.setattr("harness.adapters.factory.build_adapter", lambda key, device: FakeAdapter(key, SPANS))
    monkeypatch.setattr(E, "_ADAPTER_CACHE", {})
    c = HarnessConfig.load()
    c.raw["models"]["obi_roberta"]["weights_sha256"] = "0" * 64
    with pytest.raises(EngineStartupError, match="weight hash mismatch"):
        PHIEngine(c, registry_store=store())
    assert make(c).weight_hashes["obi_roberta"] == "injected"


def test_every_response_is_stamped():
    resp = make().analyze(request())
    s = resp.stamps
    assert s.code_fingerprint and s.code_commit and s.config_sha256 == resp.config_sha256
    assert s.models["obi_roberta"]["revision"] == "fake-obi_roberta-rev"
    json.loads(resp.model_dump_json())
