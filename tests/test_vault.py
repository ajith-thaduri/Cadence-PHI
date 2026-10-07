"""P5 token vault: encrypted at rest, tamper-evident, fail-closed without a key, TTL, audited without values,
and consistent tokens within a conversation scope (never across scopes)."""
from __future__ import annotations

import base64
import json
import os
import secrets
import time

import pytest

from harness.phi.config import HarnessConfig
from harness.phi.failsafe import EngineFailure, EngineStartupError
from harness.phi.redaction import TokenVault
from harness.phi.vault import EncryptedFileVaultStore, MemoryVaultStore, load_key
from test_phi_harness import SPANS, TEXT, FakeAdapter, request, store

SURFACE = "Maria Hernandez"


def _vault(vid="vault_abc123"):
    v = TokenVault(vault_id=vid, request_id="req_1", tokens={"[NAME_1]": SURFACE})
    v.ids[("NAME", "mariahernandez")] = "[NAME_1]"
    v.counters["NAME"] = 1
    return v


def test_encrypted_file_roundtrip_and_no_plaintext_on_disk(tmp_path):
    st = EncryptedFileVaultStore(tmp_path, secrets.token_bytes(32), ttl_seconds=60)
    st.put(_vault())
    raw = (tmp_path / "vault_abc123.vault").read_bytes()
    assert SURFACE.encode() not in raw and b"NAME_1" not in raw
    assert oct(os.stat(tmp_path / "vault_abc123.vault").st_mode)[-3:] == "600"
    got = st.get("vault_abc123")
    assert got.tokens == {"[NAME_1]": SURFACE} and got.counters == {"NAME": 1}
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "put" in audit and "get" in audit and SURFACE not in audit and "NAME_1" not in audit


def test_tampered_or_swapped_vault_fails_closed(tmp_path):
    st = EncryptedFileVaultStore(tmp_path, secrets.token_bytes(32), ttl_seconds=60)
    st.put(_vault("vault_a1"))
    st.put(_vault("vault_b2"))
    f = tmp_path / "vault_a1.vault"
    b = bytearray(f.read_bytes())
    b[-1] ^= 1
    f.write_bytes(bytes(b))
    with pytest.raises(EngineFailure) as e:
        st.get("vault_a1")
    assert e.value.code == "vault_integrity_failed"
    (tmp_path / "vault_a1.vault").write_bytes((tmp_path / "vault_b2.vault").read_bytes())   # swap: AAD binds the id
    with pytest.raises(EngineFailure):
        st.get("vault_a1")
    with pytest.raises(EngineFailure):
        st.get("../etc/passwd")


def test_wrong_key_cannot_read(tmp_path):
    EncryptedFileVaultStore(tmp_path, secrets.token_bytes(32), 60).put(_vault())
    with pytest.raises(EngineFailure):
        EncryptedFileVaultStore(tmp_path, secrets.token_bytes(32), 60).get("vault_abc123")


def test_ttl_expiry(tmp_path):
    st = EncryptedFileVaultStore(tmp_path, secrets.token_bytes(32), ttl_seconds=-1)
    st.put(_vault())
    assert st.get("vault_abc123") is None and not (tmp_path / "vault_abc123.vault").exists()
    m = MemoryVaultStore(ttl_seconds=-1)
    m.put(_vault())
    assert m.get("vault_abc123") is None


def test_memory_store_is_bounded():
    m = MemoryVaultStore(ttl_seconds=60, max_vaults=2)
    for i in range(5):
        m.put(_vault(f"vault_{i:04d}"))
    assert len(m) == 2 and m.get("vault_0000") is None and m.get("vault_0004") is not None


def test_key_loading(monkeypatch):
    monkeypatch.delenv("PHI_VAULT_KEY", raising=False)
    with pytest.raises(EngineFailure, match="") as e:
        load_key("env:PHI_VAULT_KEY")
    assert e.value.code == "vault_key_missing"
    monkeypatch.setenv("PHI_VAULT_KEY", "too-short")
    with pytest.raises(EngineFailure):
        load_key("env:PHI_VAULT_KEY")
    k = secrets.token_bytes(32)
    monkeypatch.setenv("PHI_VAULT_KEY", base64.b64encode(k).decode())
    assert load_key("env:PHI_VAULT_KEY") == k
    monkeypatch.setenv("PHI_VAULT_KEY", k.hex())
    assert load_key("env:PHI_VAULT_KEY") == k


def _engine(cfg):
    from harness.phi.engine import PHIEngine
    return PHIEngine(cfg, registry_store=store(), adapters={m: FakeAdapter(m, SPANS) for m in cfg.raw["models"]})


def test_engine_refuses_to_start_without_vault_key(monkeypatch, tmp_path):
    monkeypatch.delenv("PHI_VAULT_KEY", raising=False)
    cfg = HarnessConfig.load(env="test")
    cfg.set_value("redaction.vault.backend", "encrypted_file")
    cfg.set_value("redaction.vault.path", str(tmp_path))
    with pytest.raises(EngineStartupError, match="vault"):
        _engine(cfg)


def _redact(eng, text, conv):
    return eng.redact(request(text, request_id=f"req_{abs(hash((text, conv))) % 10**8}", options={"mode": "redact"},
                              metadata={"conversation_id": conv} if conv else {}))


def test_conversation_scope_keeps_tokens_consistent(monkeypatch, tmp_path):
    monkeypatch.setenv("PHI_VAULT_KEY", secrets.token_bytes(32).hex())
    cfg = HarnessConfig.load(env="test")
    cfg.set_value("redaction.vault.backend", "encrypted_file")
    cfg.set_value("redaction.vault.path", str(tmp_path))
    cfg.set_value("redaction.consistency_scope", "conversation")
    eng = _engine(cfg)
    r1 = _redact(eng, TEXT, "conv_1")
    r2 = _redact(eng, TEXT + " Follow up next week.", "conv_1")
    r3 = _redact(eng, TEXT, "conv_2")
    t1, t2 = r1.output.sanitized_text, r2.output.sanitized_text
    assert r1.status == r2.status == "ok" and SURFACE not in t1 and SURFACE not in t2
    assert r1.output.vault_id == r2.output.vault_id != r3.output.vault_id
    tok = next(x for x in ("[NAME_1]", "[NAME_2]", "[NAME_3]") if x in t1)
    assert tok in t2                                                        # same person, same token, next turn
    assert SURFACE in eng.restore(r2.output.vault_id, f"Reminder for {tok}.")
    assert not any(SURFACE.encode() in f.read_bytes() for f in tmp_path.glob("*.vault"))
    with pytest.raises(KeyError):
        eng.restore("vault_unknown_000", "x")


def test_document_scope_without_conversation_id_falls_back(monkeypatch):
    cfg = HarnessConfig.load(env="test")
    cfg.set_value("redaction.consistency_scope", "conversation")
    eng = _engine(cfg)
    a, b = _redact(eng, TEXT, None), _redact(eng, TEXT + " ok", None)
    assert a.output.vault_id != b.output.vault_id
