"""Engine lifecycle for the console.

- One production engine per process, built from configs/phi for ui.engine_env (startup self-test as configured).
- What-if engines: per-request, NON-persistent overrides (entities, models, recognizers, thresholds, scope) applied
  to a COPY of the configuration; a small LRU of them is cached (weights are shared process-wide). They skip the
  startup self-test - a what-if may legitimately disable a sentinel entity - and every response says so.
- Scoped registry: the caller may supply the member's known details for ONE request. They live in an ephemeral
  layer of the registry store under a random context id and are removed when the request ends.
- Engines are not assumed thread-safe: each one is used under its own lock.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from harness.phi.config import HarnessConfig
from harness.phi.engine import PHIEngine
from harness.phi.outbound_guard import OutboundGuard
from harness.phi.registry_store import InMemoryRegistryStore, RegistryAccessError
from harness_ui.schemas import KnownDetails, WhatIf
from harness_ui.settings import UISettings


class EphemeralRegistryStore(InMemoryRegistryStore):
    """Subjects added for a single request; removed afterwards. Directories are shared (none by default)."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    @contextmanager
    def scoped(self, details: Optional[KnownDetails]) -> Iterator[Optional[str]]:
        if details is None or details.is_empty():
            yield None
            return
        ctx = f"ctx_ui_{secrets.token_hex(8)}"
        with self._lock:
            self._subjects[ctx] = details.to_registry()
        try:
            yield ctx
        finally:
            with self._lock:
                self._subjects.pop(ctx, None)

    def subject(self, subject_context_id: str) -> dict[str, Any]:
        with self._lock:
            if subject_context_id not in self._subjects:
                raise RegistryAccessError("unknown subject context")
            return self._subjects[subject_context_id]


class WhatIfError(ValueError):
    pass


def apply_what_if(base: HarnessConfig, w: WhatIf) -> HarnessConfig:
    cfg = base.copy()
    ents, recs, models = set(cfg.entity_ids), set(cfg.raw["recognizers"]), set(cfg.raw["models"])
    bad = ([e for e in w.disable_entities + w.enable_entities if e not in ents]
           + [r for r in w.disable_recognizers if r not in recs]
           + [m for m in (w.models or []) if m not in models])
    if bad:
        raise WhatIfError(f"unknown names in what-if: {sorted(set(bad))}")
    if w.enable_entities:
        cfg.set_entities(w.enable_entities, True)
    if w.disable_entities:
        cfg.set_entities(w.disable_entities, False)
    if w.disable_recognizers:
        cfg.set_recognizers(w.disable_recognizers, False)
    if w.models is not None:
        cfg.only_models(w.models)
    for key, v in w.thresholds.items():
        kind, _, name = key.partition(":")
        if kind == "entity" and name in ents:
            cfg.set_value(f"thresholds.entity.{name}", float(v))
        elif kind == "model" and name in models:
            cfg.set_value(f"thresholds.model.{name}", float(v))
        else:
            raise WhatIfError(f"threshold key must be entity:<ENTITY> or model:<MODEL>, got {key!r}")
    try:
        cfg.validate()
    except Exception as e:  # noqa: BLE001 - surfaced to the user as a validation message (config text, no PHI)
        raise WhatIfError(f"what-if configuration is invalid: {e}") from None
    return cfg


class EngineManager:
    def __init__(self, settings: UISettings) -> None:
        self.s = settings
        self.store = EphemeralRegistryStore()
        self.base_cfg = HarnessConfig.load(None, env=settings.engine_env)
        self._device: Optional[str] = None
        self._base: Optional[PHIEngine] = None
        self._guard: Optional[OutboundGuard] = None
        self._what_if: OrderedDict[str, PHIEngine] = OrderedDict()
        self._locks: dict[int, threading.Lock] = {}
        self._build_lock = threading.Lock()
        self.error: Optional[str] = None

    # ------------------------------------------------------------------------------------------ lifecycle
    @property
    def device(self) -> str:
        if self._device is None:
            from harness.devices import detect_device
            self._device = detect_device(self.s.device)
        return self._device

    def base(self) -> PHIEngine:
        if self._base is None:
            with self._build_lock:
                if self._base is None:
                    try:
                        self._base = PHIEngine(self.base_cfg, device=self.device, registry_store=self.store)
                        self.error = None
                    except Exception as e:  # noqa: BLE001 - startup failure is shown on the status page
                        self.error = f"{type(e).__name__}: {str(e)[:300]}"
                        raise
        return self._base

    @property
    def ready(self) -> bool:
        return self._base is not None

    def guard(self) -> OutboundGuard:
        if self._guard is None:
            with self._build_lock:
                if self._guard is None:
                    self._guard = OutboundGuard(self.base_cfg, registry_store=self.store)
        return self._guard

    def engine_for(self, w: Optional[WhatIf]) -> tuple[PHIEngine, bool]:
        if w is None or w.is_empty():
            return self.base(), False
        if not self.s.features.what_if:
            raise WhatIfError("what-if analysis is disabled in configs/ui/ui.yaml")
        key = hashlib.sha256(json.dumps(w.model_dump(), sort_keys=True).encode()).hexdigest()[:16]
        with self._build_lock:
            if key in self._what_if:
                self._what_if.move_to_end(key)
                return self._what_if[key], True
        cfg = apply_what_if(self.base_cfg, w)
        self.base()                                   # weights are loaded once, through the production engine
        eng = PHIEngine(cfg, device=self.device, registry_store=self.store, self_test=False)
        eng.vault_store = self.base().vault_store     # one vault: what-if tokens are restorable like any others
        with self._build_lock:
            self._what_if[key] = eng
            while len(self._what_if) > max(self.s.limits.what_if_engines, 0):
                self._what_if.popitem(last=False)
        return eng, True

    @contextmanager
    def using(self, eng: PHIEngine) -> Iterator[PHIEngine]:
        with self._build_lock:
            lock = self._locks.setdefault(id(eng), threading.Lock())
        with lock:
            yield eng

    def reload(self) -> None:
        """Re-read configs/phi (after a reviewed change was deployed) and rebuild engines."""
        with self._build_lock:
            self.base_cfg = HarnessConfig.load(None, env=self.s.engine_env)
            self._base, self._guard = None, None
            self._what_if.clear()
            self._locks.clear()
        self.base()
