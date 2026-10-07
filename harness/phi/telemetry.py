"""PHI-free telemetry: structured events with allow-listed fields, an enforcing log filter,
keyed hashing, and in-process metrics counters (exportable as JSON or Prometheus text).

Rules (NIST SP 800-53 AU / SI-11; HIPAA minimum necessary):
  - log records from the `harness` namespace may only carry numbers, booleans, None and short
    identifier-like strings (ids, hashes, recognizer names). Anything else is replaced by
    "[blocked]" and counted in metrics["log_records_blocked"] - text cannot reach a log by accident.
  - text fingerprints in logs are HMAC-SHA256 keyed with PHI_LOG_HMAC_KEY (per-process random key
    when unset: logs are then unlinkable across processes, never brute-forceable).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from collections import defaultdict
from typing import Any

SAFE_STR = re.compile(r"^[A-Za-z0-9_.:/\-+=,\[\]]{0,96}$")
SAFE_LITERAL = re.compile(r"^[A-Za-z0-9_.:/\-+=,\[\]()%' ]{0,200}$")     # argument-free messages: short literals only
ALLOWED_FIELDS = {
    "event", "request_id", "text_hmac", "text_length", "language", "modality", "status", "failure_class",
    "stage", "code", "detections", "candidates", "accepted", "rejected", "overlap_groups", "total_ms",
    "model_ms", "recognizer_ms", "policy_ms", "redaction_ms", "replacements", "config_sha256", "code_commit",
    "code_fingerprint", "catalog_version", "models", "recognizers", "vault_id", "operator", "entities_by_type",
    "registry_versions", "subject_ref_hmac", "caller", "action", "count", "guard_hits", "self_test", "purpose",
    "shadow_disagreements", "canary",
}
_KEY = os.environ.get("PHI_LOG_HMAC_KEY", "").encode() or secrets.token_bytes(32)
_LOCK = threading.Lock()
METRICS: dict[str, Any] = defaultdict(int)
LATENCY_BUCKETS_MS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]


def keyed_hash(value: str, n: int = 16) -> str:
    return hmac.new(_KEY, value.encode("utf-8"), hashlib.sha256).hexdigest()[:n]


def _safe(v: Any) -> bool:
    if v is None or isinstance(v, (bool, int, float)):
        return True
    if isinstance(v, str):
        return bool(SAFE_STR.match(v))
    if isinstance(v, (list, tuple)):
        return len(v) <= 64 and all(_safe(x) for x in v)
    if isinstance(v, dict):
        return len(v) <= 64 and all(_safe(k) and _safe(x) for k, x in v.items())
    return False


class PHISafeLogFilter(logging.Filter):
    """Enforcing filter for the `harness` logger tree. Never lets free text through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith("harness"):
            return True
        if getattr(record, "phi_telemetry", False):          # built by event(): fields already allow-listed
            return True
        args = record.args if isinstance(record.args, tuple) else ((record.args,) if record.args else ())
        msg_ok = isinstance(record.msg, str) and (args or SAFE_LITERAL.match(record.msg))
        if not msg_ok or any(not _safe(a) for a in args) or (record.exc_info and record.name.startswith("harness.phi")):
            with _LOCK:
                METRICS["log_records_blocked"] += 1
            record.msg = "[blocked] log record from %s withheld by PHI-safe filter"
            record.args = (record.name,)
            record.exc_info = None
            record.exc_text = None
        return True


_INSTALLED = False


def install() -> None:
    """Attach the enforcing filter to the harness logger tree and silence verbose third parties."""
    global _INSTALLED
    if _INSTALLED:
        return
    flt = PHISafeLogFilter()
    for name in ("harness", "harness.phi", "harness.phi.engine"):
        logging.getLogger(name).addFilter(flt)
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(flt)
    logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)
    logging.getLogger("decision_process").disabled = True     # Presidio's raw-text decision logger
    _INSTALLED = True


def event(logger: logging.Logger, name: str, **fields: Any) -> None:
    """Structured, allow-listed event. Unknown or unsafe fields are dropped (and counted)."""
    clean: dict[str, Any] = {"event": name}
    for k, v in fields.items():
        if k in ALLOWED_FIELDS and _safe(v):
            clean[k] = v
        else:
            with _LOCK:
                METRICS["telemetry_fields_dropped"] += 1
    logger.info(json.dumps(clean, sort_keys=True, separators=(",", ":")) if _safe_json(clean) else "{}",
                extra={"phi_telemetry": True})


def _safe_json(d: dict) -> bool:
    return len(json.dumps(d)) < 4000


def count(name: str, n: int = 1) -> None:
    with _LOCK:
        METRICS[name] += n


def observe_latency(stage: str, ms: float) -> None:
    with _LOCK:
        for b in LATENCY_BUCKETS_MS:
            if ms <= b:
                METRICS[f"latency_ms_bucket{{stage={stage},le={b}}}"] += 1
        METRICS[f"latency_ms_bucket{{stage={stage},le=+Inf}}"] += 1
        METRICS[f"latency_ms_sum{{stage={stage}}}"] += ms
        METRICS[f"latency_ms_count{{stage={stage}}}"] += 1


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return dict(METRICS)


def prometheus_text(prefix: str = "phi_engine") -> str:
    lines = []
    for k, v in sorted(snapshot().items()):
        name, _, labels = k.partition("{")
        lbl = ("{" + ",".join(f'{a}="{b}"' for a, b in (p.split("=", 1) for p in labels.rstrip("}").split(","))) + "}") if labels else ""
        lines.append(f"{prefix}_{name}{lbl} {v}")
    return "\n".join(lines) + "\n"


def reset_metrics() -> None:
    with _LOCK:
        METRICS.clear()
