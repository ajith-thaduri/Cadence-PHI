"""Fail-closed behaviour (NIST SP 800-53 SC-24 fail-in-known-state, SI-17 fail-safe, SI-11 error handling).

Every public engine call runs under `guard()`. Any failure in detection or redaction is mapped to a
FailureClass and handled by the configured policy (`policy/failure.yaml`, or `failure:` in a legacy
single-file config):

    block                  -> response.status = "blocked"; no detections, no sanitized text.
                              The caller MUST NOT forward the text (e.g. to an LLM).
    conservative_fallback  -> re-run with ML models disabled (rules + registry only), mark the
                              response status = "fallback"; if that fails too -> block.
    reject                 -> (oversize input) status = "blocked" with failure_class oversize_input.
    refuse_start           -> (invalid config / failed self-test) the engine raises at construction.

Silent pass-through, silent truncation and partial results are never produced. Failure details
carry a class, a stage and a stable code - never exception text, which could contain PHI (SI-11).
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

_EXECUTOR = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="phi-model")


class FailureClass(str, Enum):
    model_error = "model_error"
    recognizer_error = "recognizer_error"
    timeout = "timeout"
    oversize_input = "oversize_input"
    unsupported_language = "unsupported_language"
    registry_unavailable = "registry_unavailable"
    vault_unavailable = "vault_unavailable"
    key_unavailable = "key_unavailable"
    redaction_error = "redaction_error"
    internal_error = "internal_error"


class EngineFailure(Exception):
    """Raised inside the engine; carries no PHI (message is a stable code)."""

    def __init__(self, failure_class: FailureClass, stage: str, code: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.failure_class = failure_class
        self.stage = stage
        self.code = code
        self.retryable = retryable


class EngineStartupError(RuntimeError):
    """Configuration invalid or startup self-test failed: the engine must not serve traffic."""


@dataclass
class FailurePolicy:
    on_model_error: str = "block"
    on_recognizer_error: str = "block"
    on_timeout: str = "block"
    on_oversize_input: str = "reject"
    on_unsupported_language: str = "reject"
    on_registry_unavailable: str = "block"
    on_vault_unavailable: str = "block"
    on_key_unavailable: str = "block"
    on_redaction_error: str = "block"
    on_internal_error: str = "block"
    deadline_total_ms: float = 30000.0
    deadline_model_ms: float = 20000.0
    max_input_chars: int = 50000

    VALID = {"block", "conservative_fallback", "reject"}

    @classmethod
    def from_config(cls, raw: Optional[dict[str, Any]]) -> "FailurePolicy":
        raw = raw or {}
        p = cls()
        for k, v in raw.items():
            if k == "deadline_ms":
                p.deadline_total_ms = float(v.get("total", p.deadline_total_ms))
                p.deadline_model_ms = float(v.get("model", p.deadline_model_ms))
            elif k == "max_input_chars":
                p.max_input_chars = int(v)
            elif k.startswith("on_") and hasattr(p, k):
                if v not in cls.VALID:
                    raise ValueError(f"failure policy {k}={v!r} not in {sorted(cls.VALID)}")
                setattr(p, k, v)
            elif k in ("startup_self_test", "on_config_invalid"):
                continue
            else:
                raise ValueError(f"unknown failure policy key {k!r}")
        return p

    def action_for(self, fc: FailureClass) -> str:
        return getattr(self, f"on_{fc.value}", "block")


class Deadline:
    """Absolute per-request deadline; stages call check() between units of work."""

    def __init__(self, total_ms: float) -> None:
        self.t0 = time.perf_counter()
        self.end = self.t0 + total_ms / 1000.0

    def remaining_s(self) -> float:
        return max(0.0, self.end - time.perf_counter())

    def check(self, stage: str) -> None:
        if time.perf_counter() > self.end:
            raise EngineFailure(FailureClass.timeout, stage, "deadline_exceeded", retryable=True)


def run_with_timeout(fn: Callable[[], Any], timeout_s: float, stage: str, failure_class: FailureClass) -> Any:
    """Run fn in the shared worker pool with a timeout. A timed-out call is abandoned (its result is
    discarded) and the request fails closed; the worker thread finishes in the background."""
    fut = _EXECUTOR.submit(fn)
    try:
        return fut.result(timeout=max(0.001, timeout_s))
    except cf.TimeoutError:
        fut.cancel()
        raise EngineFailure(FailureClass.timeout, stage, "stage_timeout", retryable=True) from None
    except EngineFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - original exception TEXT may contain PHI; only the class name is kept
        import os
        if os.environ.get("PHI_DEBUG_RAISE") == "1":
            raise
        raise EngineFailure(failure_class, stage, f"{failure_class.value}:{type(exc).__name__}") from None
