"""Token vault stores: where the re-identification map (token -> original surface) lives.

Backends (policy/redaction.yaml `redaction.vault.backend`):
  memory          bounded (max_vaults, LRU) with TTL - development / tests / single-process use
  encrypted_file  one file per vault, AES-256-GCM (cryptography), random 96-bit nonce, vault_id as associated
                  data, atomic write, 0600 files in a 0700 directory; TTL enforced on read and by purge()
Key management: key_ref "env:NAME" -> 32-byte key (base64 / urlsafe-base64 / 64 hex chars). A missing or
malformed key is a STARTUP failure (the engine refuses to serve) - there is no plaintext fallback.

Consistency scope (`redaction.consistency_scope`) - which requests share token numbering:
  document      every request starts fresh ([NAME_1] in two requests may be different people)
  conversation  requests with the same metadata.conversation_id share one vault: the same surface keeps the
                same token across turns, so an LLM sees one consistent [NAME_1]
  member        requests with the same registry_context.subject_context_id share one vault
Never global. A request without the scope key falls back to `document` (counted: vault_scope_fallback).

Audit (`audit: true`): every put / restore / expiry is recorded with vault_id, action, actor, purpose and time -
never tokens or surfaces. encrypted_file appends to <path>/audit.jsonl; both backends emit a telemetry event.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Protocol

from harness.phi import telemetry
from harness.phi.contracts import stable_id
from harness.phi.failsafe import EngineFailure, FailureClass
from harness.phi.redaction import TokenVault

log = logging.getLogger("harness.phi.vault")
_ID_RX = re.compile(r"^[A-Za-z0-9_\-]{4,128}$")


class VaultStore(Protocol):
    def put(self, vault: TokenVault, actor: str = "engine") -> None: ...
    def get(self, vault_id: str, actor: str = "engine", purpose: str = "scope_continuation") -> Optional[TokenVault]: ...
    def purge(self) -> int: ...


def scope_key(scope: str, metadata: dict[str, Any], subject_context_id: Optional[str]) -> Optional[str]:
    if scope == "conversation":
        v = metadata.get("conversation_id")
    elif scope == "member":
        v = subject_context_id
    else:
        return None
    return str(v) if v not in (None, "") else None


def scoped_vault_id(scope: str, key: str) -> str:
    return stable_id("vault", scope, key)


def _serialize(v: TokenVault, expires_at: float) -> bytes:
    return json.dumps({"vault_id": v.vault_id, "request_id": v.request_id, "tokens": v.tokens,
                       "ids": [[e, s, t] for (e, s), t in v.ids.items()], "counters": v.counters,
                       "expires_at": expires_at}, separators=(",", ":")).encode()


def _deserialize(b: bytes) -> tuple[TokenVault, float]:
    d = json.loads(b)
    v = TokenVault(vault_id=d["vault_id"], request_id=d["request_id"], tokens=dict(d["tokens"]))
    v.ids = {(e, s): t for e, s, t in d["ids"]}
    v.counters = dict(d["counters"])
    return v, float(d["expires_at"])


class _Audit:
    def __init__(self, enabled: bool, path: Optional[Path]) -> None:
        self.enabled, self.path = enabled, path
        self._lock = threading.Lock()

    def __call__(self, action: str, vault_id: str, actor: str, purpose: str = "") -> None:
        if not self.enabled:
            return
        telemetry.event(log, "vault_audit", action=action, vault_id=vault_id, caller=actor)
        telemetry.count(f"vault_{action}")
        if self.path is not None:
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action, "vault_id": vault_id,
                   "actor": actor, "purpose": purpose}
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")


class MemoryVaultStore:
    """Bounded LRU with TTL. Lost on restart by design."""

    def __init__(self, ttl_seconds: float, max_vaults: int = 10_000, audit: bool = True) -> None:
        self.ttl, self.max = float(ttl_seconds), int(max_vaults)
        self._d: OrderedDict[str, tuple[TokenVault, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.audit = _Audit(audit, None)

    def put(self, vault: TokenVault, actor: str = "engine") -> None:
        with self._lock:
            self._d[vault.vault_id] = (vault, time.time() + self.ttl)
            self._d.move_to_end(vault.vault_id)
            while len(self._d) > self.max:
                old, _ = self._d.popitem(last=False)
                self.audit("evict", old, "store")
        self.audit("put", vault.vault_id, actor)

    def get(self, vault_id: str, actor: str = "engine", purpose: str = "scope_continuation") -> Optional[TokenVault]:
        with self._lock:
            item = self._d.get(vault_id)
            if item is None:
                return None
            v, exp = item
            if exp < time.time():
                del self._d[vault_id]
                expired = True
            else:
                self._d.move_to_end(vault_id)
                expired = False
        if expired:
            self.audit("expire", vault_id, "store")
            return None
        self.audit("get", vault_id, actor, purpose)
        return v

    def purge(self) -> int:
        now = time.time()
        with self._lock:
            dead = [k for k, (_, e) in self._d.items() if e < now]
            for k in dead:
                del self._d[k]
        for k in dead:
            self.audit("expire", k, "store")
        return len(dead)

    def __len__(self) -> int:
        return len(self._d)


def load_key(key_ref: str) -> bytes:
    """key_ref 'env:NAME' -> 32 bytes. Raises EngineFailure(key_unavailable); the value is never logged."""
    if not key_ref.startswith("env:"):
        raise EngineFailure(FailureClass.key_unavailable, "startup", "vault_key_ref_unsupported")
    raw = os.environ.get(key_ref[4:], "").strip()
    if not raw:
        raise EngineFailure(FailureClass.key_unavailable, "startup", "vault_key_missing")
    for dec in (lambda s: bytes.fromhex(s), base64.b64decode, base64.urlsafe_b64decode):
        try:
            k = dec(raw)
        except (ValueError, binascii.Error):
            continue
        if len(k) == 32:
            return k
    raise EngineFailure(FailureClass.key_unavailable, "startup", "vault_key_malformed")


class EncryptedFileVaultStore:
    """AES-256-GCM per vault file. The plaintext map never touches disk."""

    def __init__(self, path: str | Path, key: bytes, ttl_seconds: float, audit: bool = True) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self._aead = AESGCM(key)
        self.ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self.audit = _Audit(audit, self.dir / "audit.jsonl")

    def _file(self, vault_id: str) -> Path:
        if not _ID_RX.match(vault_id):                 # vault ids are engine-issued; reject path tricks
            raise EngineFailure(FailureClass.vault_unavailable, "vault", "invalid_vault_id")
        return self.dir / f"{vault_id}.vault"

    def put(self, vault: TokenVault, actor: str = "engine") -> None:
        nonce = secrets.token_bytes(12)
        blob = nonce + self._aead.encrypt(nonce, _serialize(vault, time.time() + self.ttl), vault.vault_id.encode())
        f = self._file(vault.vault_id)
        tmp = f.with_suffix(f".tmp{secrets.token_hex(4)}")
        try:
            with self._lock:
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(blob)
                os.replace(tmp, f)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise EngineFailure(FailureClass.vault_unavailable, "vault", "vault_write_failed") from None
        self.audit("put", vault.vault_id, actor)

    def get(self, vault_id: str, actor: str = "engine", purpose: str = "scope_continuation") -> Optional[TokenVault]:
        f = self._file(vault_id)
        try:
            blob = f.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise EngineFailure(FailureClass.vault_unavailable, "vault", "vault_read_failed") from None
        try:
            v, exp = _deserialize(self._aead.decrypt(blob[:12], blob[12:], vault_id.encode()))
        except Exception:  # noqa: BLE001 - InvalidTag / corrupt file: never fall back to anything
            raise EngineFailure(FailureClass.vault_unavailable, "vault", "vault_integrity_failed") from None
        if exp < time.time():
            f.unlink(missing_ok=True)
            self.audit("expire", vault_id, "store")
            return None
        self.audit("get", vault_id, actor, purpose)
        return v

    def purge(self) -> int:
        n = 0
        for f in self.dir.glob("*.vault"):
            try:
                if self.get(f.stem, "store", "purge") is None:
                    n += 1
            except EngineFailure:
                continue
        return n


def make_store(cfg_raw: dict[str, Any]) -> VaultStore:
    v = ((cfg_raw.get("redaction") or {}).get("vault") or {})
    ttl = float(v.get("ttl_days", 30)) * 86400
    audit = bool(v.get("audit", True))
    if v.get("backend", "memory") == "encrypted_file":
        return EncryptedFileVaultStore(v.get("path", "vault"), load_key(v.get("key_ref", "env:PHI_VAULT_KEY")), ttl, audit)
    return MemoryVaultStore(ttl, int(v.get("max_vaults", 10_000)), audit)
