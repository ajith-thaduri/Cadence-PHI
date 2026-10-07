"""P5 outbound guard: independent deterministic re-scan of sanitized text before egress."""
from __future__ import annotations

from harness.phi.config import HarnessConfig
from harness.phi.engine import PHIEngine
from harness.phi.outbound_guard import OutboundGuard
from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store


def _guard(**over):
    cfg = HarnessConfig.load(env="test")
    for k, v in over.items():
        cfg.set_value(k, v)
    return cfg, OutboundGuard(cfg, registry_store=store())


def test_clean_tokenized_output_passes():
    cfg, g = _guard()
    eng = PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})
    r = eng.redact(request(TEXT, options={"mode": "redact"}))
    issued = list(eng.vault_store.get(r.output.vault_id).tokens)
    res = g.check(r.output.sanitized_text, issued, registry_context=request().registry_context)
    assert res.allowed, [(h.entity_type, h.recognizers) for h in res.hits]


def test_leaked_identifier_is_blocked_without_text_in_result():
    _, g = _guard()
    res = g.check("Summary for [NAME_1]: call back at 415-555-0100, MRN 4471-2290.", ["[NAME_1]"])
    assert not res.allowed
    assert {h.entity_type for h in res.blocking_hits} >= {"PHONE", "MRN"}
    assert all(not hasattr(h, "text") for h in res.hits)


def test_registry_value_leak_is_blocked():
    _, g = _guard()
    res = g.check("Member Maria Hernandez asked about the refill.", [], registry_context=request().registry_context)
    assert not res.allowed and any("registry" in " ".join(h.recognizers) for h in res.hits)


def test_guard_uses_no_model_and_does_not_mutate_primary_config():
    cfg, g = _guard()
    assert g._engine.models == {}
    assert cfg.enabled_models                       # the primary config still has its model enabled


def test_flag_action_records_but_allows():
    _, g = _guard(**{"outbound_guard.action": "flag"})
    res = g.check("Call 415-555-0100.", [])
    assert res.allowed and res.hits


def test_guard_failure_fails_closed(monkeypatch):
    _, g = _guard()
    monkeypatch.setattr(g._engine, "analyze", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res = g.check("anything", [])
    assert not res.allowed and res.status == "blocked_guard_error"
