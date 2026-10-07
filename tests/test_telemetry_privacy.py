"""T6 security / privacy: PHI never reaches logs, metrics or blocked responses; no network egress."""
from __future__ import annotations

import logging
import socket

import pytest

from harness.phi import telemetry
from harness.phi.config import HarnessConfig
from harness.phi.contracts import RedactionRequest
from test_failsafe import ExplodingAdapter, make
from test_phi_harness import TEXT, request

SENTINELS = ("Maria", "Hernandez", "Lucia", "Pierce", "nine one seven", "five five five")


def _all_paths(eng):
    eng.analyze(request(options={"mode": "detect"}))
    eng.analyze(request(options={"mode": "debug", "include_text": True}))     # debug: text in the RESPONSE only
    r = eng.redact(request(options={"mode": "redact"}))
    if r.status == "ok":
        eng.restore(r.output.vault_id, r.output.sanitized_text)
    eng.redact(RedactionRequest(request_id="req_t", original_text=TEXT, detections=r.detections))
    eng.analyze(request(registry_context={"subject_context_id": "ctx_missing"}))   # blocked path


def test_no_phi_in_logs_on_any_path(caplog):
    with caplog.at_level(logging.DEBUG):
        _all_paths(make())
        _all_paths(make(adapter_cls=ExplodingAdapter))
    for s in SENTINELS:
        assert s not in caplog.text
    assert "phi.request" in caplog.text and "phi.blocked" in caplog.text          # telemetry is emitted


def test_log_filter_blocks_free_text(caplog):
    telemetry.install()
    lg = logging.getLogger("harness.phi.somewhere")
    before = telemetry.snapshot().get("log_records_blocked", 0)
    with caplog.at_level(logging.INFO):
        lg.info("member %s called", "Maria Hernandez from Fresno")              # a careless log call
        lg.info("request %s ok", "req_123")                                       # an identifier is fine
    assert "Maria" not in caplog.text and "req_123" in caplog.text
    assert telemetry.snapshot()["log_records_blocked"] == before + 1


def test_event_drops_unknown_and_unsafe_fields(caplog):
    lg = logging.getLogger("harness.phi.test")
    with caplog.at_level(logging.INFO):
        telemetry.event(lg, "x", request_id="req_1", text="Maria Hernandez", detections=3, note="free text here")
    assert "Maria" not in caplog.text and '"detections":3' in caplog.text and "free text" not in caplog.text


def test_keyed_hash_is_not_plain_sha():
    import hashlib
    v = "01/01/1950"
    assert telemetry.keyed_hash(v) != hashlib.sha256(v.encode()).hexdigest()[:16]


def test_metrics_export_contains_no_phi():
    _all_paths(make())
    txt = telemetry.prometheus_text()
    assert "phi_engine_requests_total" in txt
    for s in SENTINELS:
        assert s not in txt


def test_no_network_egress(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network egress attempted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    eng = make()
    assert eng.redact(request(options={"mode": "redact"})).status == "ok"


def test_presidio_decision_logger_disabled():
    telemetry.install()
    assert logging.getLogger("decision_process").disabled


def test_runtime_source_has_no_formatted_log_calls():
    """Static lint (T6): log calls in the runtime package must use a literal message with %-args
    (checked at runtime by the filter) or telemetry.event - never f-strings, .format() or string
    concatenation, which the runtime filter cannot inspect."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "harness" / "phi"
    runtime = [p for p in root.rglob("*.py") if p.name not in {"analyze.py", "cli.py", "evaluate.py", "calibrate.py",
                                                                "compare.py", "export.py", "matrix.py", "report.py", "datasets.py",
                                                                "telemetry.py"}]   # telemetry.event is the sanitiser itself
    bad = []
    for p in runtime:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr in {"debug", "info", "warning", "error", "exception", "critical"} and \
                    isinstance(node.func.value, ast.Name) and node.func.value.id in {"log", "logger", "lg"} and node.args:
                m = node.args[0]
                if not (isinstance(m, ast.Constant) and isinstance(m.value, str)) and not (
                        isinstance(m, ast.Call) and getattr(m.func, "attr", "") == "dumps"):
                    bad.append(f"{p.name}:{node.lineno}")
    assert not bad, f"formatted log calls in runtime code: {bad}"


# ---------------------------------------------------------------------------- data minimisation: model-hit cache
def test_serving_engine_retains_no_model_hits_by_default():
    """A serving engine must not keep PHI-derived model spans after the request (and must not grow with traffic)."""
    from harness.phi.config import HarnessConfig
    from harness.phi.engine import PHIEngine
    from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store
    cfg = HarnessConfig.load(env="test")
    eng = PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})
    eng.analyze(request(TEXT))
    assert all(len(c) == 0 for c in eng.model_caches.values())


def test_model_cache_bound_is_lru():
    from harness.phi.recognizers.model import HitCache
    c = HitCache(2)
    c["a"], c["b"] = [], []
    _ = c["a"]
    c["c"] = []
    assert "a" in c and "c" in c and "b" not in c and len(c) == 2
    off = HitCache(0)
    off["x"] = []
    assert "x" not in off
