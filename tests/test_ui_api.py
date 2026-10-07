"""Operator console API (harness_ui): privacy (no text echo, no text on disk), authentication, CSRF, roles,
allow-listed jobs, path safety and refusal of unsafe settings. The engine uses fake model adapters."""
from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

import harness_ui.engines as E
from harness.phi.engine import PHIEngine
from harness_ui import settings as S
from harness_ui.app import create_app
from harness_ui.security import hash_password, save_users
from test_phi_harness import SPANS, FakeAdapter

SECRET_TEXT = "Call Maria Hernandez at 415-555-0100 about MRN 4471-2290."


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    """Engines built by the console use fake model adapters (no weights needed in the fast tier)."""
    real = PHIEngine.__init__

    def init(self, cfg, *a, **k):
        k.setdefault("adapters", {m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})
        real(self, cfg, *a, **k)
    monkeypatch.setattr(PHIEngine, "__init__", init)
    monkeypatch.setattr(E, "PHIEngine", PHIEngine)


def make(tmp_path, **auth) -> TestClient:
    st = S.UISettings.model_validate({
        "engine_env": "test", "device": "cpu", "warm_start": False,
        "auth": {"mode": "none", **auth},
        "storage": {"jobs_dir": str(tmp_path / "jobs"), "audit_log": str(tmp_path / "audit.jsonl")},
    })
    return TestClient(create_app(st, warm=False))


def wait_job(c, jid, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


# ---------------------------------------------------------------------------- privacy
def test_analyze_never_echoes_original_text(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/v1/analyze", json={"text": SECRET_TEXT, "include_evidence": True, "known": {"name": "Maria Hernandez"}})
    assert r.status_code == 200, r.text
    d = r.json()
    body_wo_output = json.dumps({k: v for k, v in d.items() if k != "sanitized_text"})
    for secret in ("Maria", "Hernandez", "415-555-0100", "4471-2290"):
        assert secret not in body_wo_output
        assert secret not in d["sanitized_text"]
    spans = {(x["start"], x["end"]) for x in d["detections"]}
    assert (SECRET_TEXT.index("Maria"), SECRET_TEXT.index("Maria") + len("Maria Hernandez")) in spans
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "Maria" not in audit and "415-555" not in audit and '"action":"analyze"' in audit


def test_validation_errors_do_not_echo_input(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/v1/analyze", json={"text": SECRET_TEXT, "modality": "bogus"})
    assert r.status_code == 422 and "Maria" not in r.text and "modality" in r.text


def test_known_details_are_discarded_after_the_request(tmp_path):
    c = make(tmp_path)
    c.post("/api/v1/analyze", json={"text": SECRET_TEXT, "known": {"name": "Maria Hernandez", "member_ids": ["X1"]}})
    assert c.app.state.console.engines.store._subjects == {}


def test_batch_writes_only_sanitized_output(tmp_path):
    c = make(tmp_path)
    files = [("files", ("a.txt", SECRET_TEXT.encode(), "text/plain")),
             ("files", ("b.jsonl", (json.dumps({"id": "r1", "text": SECRET_TEXT}) + "\n").encode(), "application/json"))]
    j = c.post("/api/v1/batch", files=files, data={"modality": "typed"}).json()
    j = wait_job(c, j["id"])
    assert j["status"] == "succeeded" and j["progress"] == {"done": 2, "total": 2}
    out = tmp_path / "jobs" / j["id"]
    blob = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert b"Maria" not in blob and b"415-555-0100" not in blob
    z = c.get(f"/api/v1/jobs/{j['id']}/download")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_batch_rejects_unsupported_files(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/v1/batch", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 422


# ---------------------------------------------------------------------------- hardening
def test_security_headers_and_no_store(tmp_path):
    r = make(tmp_path).get("/api/v1/status")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in r.headers


def test_run_paths_cannot_escape(tmp_path):
    c = make(tmp_path)
    for bad in ("..%2F..%2Fetc", "...", "..x", ".hidden", "a%2Fb", "phi"):
        assert c.get(f"/api/v1/runs/phi/{bad}").status_code == 404, bad
    from harness_ui import results
    for bad in ("..", "../..", "../../etc", "/etc/passwd"):
        with pytest.raises(FileNotFoundError):
            results._run_dir("phi", bad)
    assert c.get("/api/v1/runs/unknown/x").status_code == 404
    assert c.get("/api/v1/docs-page/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_jobs_are_allow_listed_and_test_data_needs_a_reason(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/v1/jobs", json={"type": "rm", "params": {}}).status_code == 422
    assert c.post("/api/v1/jobs", json={"type": "checklist", "params": {"tier": "fast; rm -rf /"}}).status_code == 422
    assert c.post("/api/v1/jobs", json={"type": "checklist", "params": {"extra": 1}}).status_code == 422
    r = c.post("/api/v1/jobs", json={"type": "evaluate", "params": {"datasets": ["cadence_text"]}})
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    assert c.post("/api/v1/jobs", json={"type": "evaluate", "params": {"datasets": ["english_v1_suite"]}}).status_code == 422


def test_what_if_is_validated_and_marked(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/v1/analyze", json={"text": "hi", "what_if": {"disable_entities": ["NOPE"]}}).status_code == 422
    d = c.post("/api/v1/analyze", json={"text": SECRET_TEXT, "what_if": {"models": [], "disable_entities": ["PHONE"]}}).json()
    assert d["what_if"] is True and d["notices"] and "415-555-0100" in d["sanitized_text"]
    base = c.post("/api/v1/analyze", json={"text": SECRET_TEXT}).json()
    assert base["what_if"] is False and "415-555-0100" not in base["sanitized_text"]  # production config untouched


def test_restore_is_audited(tmp_path):
    c = make(tmp_path)
    d = c.post("/api/v1/analyze", json={"text": SECRET_TEXT}).json()
    tok = next(t for t in ("[NAME_1]", "[PHONE_1]") if t in d["sanitized_text"])
    r = c.post("/api/v1/restore", json={"vault_id": d["vault_id"], "text": f"Reply for {tok}"})
    assert r.status_code == 200 and r.json()["replaced"] == 1
    assert c.post("/api/v1/restore", json={"vault_id": "vault_nope_000", "text": "x"}).status_code == 404
    assert '"action":"restore"' in (tmp_path / "audit.jsonl").read_text()


# ---------------------------------------------------------------------------- authentication / authorisation
def test_unsafe_settings_are_refused(monkeypatch):
    with pytest.raises(ValueError, match="loopback"):
        S.UISettings.model_validate({"host": "0.0.0.0", "auth": {"mode": "none"}}).check_safe()
    monkeypatch.setenv("PHI_UI_TOKEN", "short")
    with pytest.raises(ValueError, match="24"):
        S.UISettings.model_validate({"auth": {"mode": "token"}}).check_safe()


def test_token_mode_login_session_and_csrf(tmp_path, monkeypatch):
    monkeypatch.setenv("PHI_UI_TOKEN", "t" * 32)
    c = make(tmp_path, mode="token", token_role="operator")
    assert c.get("/api/v1/status").status_code == 401
    assert c.post("/api/v1/session", json={"username": "ann", "secret": "wrong"}).status_code == 401
    assert c.post("/api/v1/session", json={"username": "ann", "secret": "t" * 32}).status_code == 200
    assert c.get("/api/v1/status").json()["user"] == {"name": "ann", "role": "operator"}
    # cookie-authenticated state change without the X-PHI-UI header is refused (CSRF)
    assert c.post("/api/v1/analyze", json={"text": "hi"}).status_code == 403
    assert c.post("/api/v1/analyze", json={"text": "hi"}, headers={"X-PHI-UI": "1"}).status_code == 200
    # operator cannot re-identify or read the audit trail
    assert c.post("/api/v1/restore", json={"vault_id": "vault_abcd", "text": "x"}, headers={"X-PHI-UI": "1"}).status_code == 403
    assert c.get("/api/v1/audit").status_code == 403
    # bearer token for automation
    c2 = make(tmp_path, mode="token")
    assert c2.get("/api/v1/status", headers={"Authorization": "Bearer " + "t" * 32}).status_code == 200


def test_users_mode_roles_and_rate_limit(tmp_path, monkeypatch):
    users = tmp_path / "users.yaml"
    save_users(users, {"vic": {"role": "viewer", "password": hash_password("correct horse 1")},
                       "gone": {"role": "admin", "password": hash_password("correct horse 2"), "disabled": True}})
    assert oct(os.stat(users).st_mode)[-3:] == "600"
    c = make(tmp_path, mode="users", users_file=str(users), max_login_failures_per_minute=3)
    assert c.post("/api/v1/session", json={"username": "gone", "secret": "correct horse 2"}).status_code == 401
    assert c.post("/api/v1/session", json={"username": "vic", "secret": "correct horse 1"}).status_code == 200
    h = {"X-PHI-UI": "1"}
    assert c.get("/api/v1/runs").status_code == 200                                         # viewer can read
    assert c.post("/api/v1/analyze", json={"text": "hi"}, headers=h).status_code == 403     # but not analyse
    assert c.post("/api/v1/jobs", json={"type": "cards", "params": {}}, headers=h).status_code == 403
    c.delete("/api/v1/session", headers=h)
    for _ in range(3):
        c.post("/api/v1/session", json={"username": "vic", "secret": "nope"})
    assert c.post("/api/v1/session", json={"username": "vic", "secret": "correct horse 1"}).status_code == 429


def test_tampered_session_cookie_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PHI_UI_TOKEN", "t" * 32)
    c = make(tmp_path, mode="token", token_role="viewer")
    c.post("/api/v1/session", json={"username": "ann", "secret": "t" * 32})
    tok = c.cookies.get("phi_ui_session")
    body, sig = tok.rsplit(".", 1)
    import base64
    forged = base64.urlsafe_b64encode(json.dumps({"u": "ann", "r": "admin", "exp": 4102444800}).encode()).decode()
    c.cookies.set("phi_ui_session", f"{forged}.{sig}")
    assert c.get("/api/v1/status").status_code == 401
