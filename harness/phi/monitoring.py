"""Production monitoring hooks (T8 / gate G9). Runtime-side: no labels, no text, no evaluation imports.

1. Canaries - fixed, conspicuously fake sentinel texts (policy/monitoring.yaml) pushed through the full engine
   path on a schedule. A canary whose sentinel value survives redaction, or a negative canary that gets
   redacted, is an immediate alert: detection silently broke (bad deploy, model file swapped, config drift).
   Canary requests carry metadata.canary=true and are excluded from the detection-rate counters.
2. Detection-rate control chart - the engine counts detections per entity x modality and characters per
   modality (telemetry). check_rates() compares a live window with the dev baseline
   (harness_eval.monitoring baseline) under a Poisson model and alerts when |z| exceeds the control limit
   in EITHER direction: a drop means missed PHI, a surge means over-redaction or an input-format change.

  from harness.phi.monitoring import run_canaries, check_rates
  report = run_canaries(engine)                     # {"passed": n, "failed": [ids], "ok": bool}
  alerts = check_rates(baseline, telemetry.snapshot(), sigma=3, min_chars=20000)
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

from harness.phi import telemetry
from harness.phi.contracts import DecisionValue, DetectionOptions, DetectionRequest, Mode

log = logging.getLogger("harness.phi.monitoring")
_DET = re.compile(r"^detections_total\{entity=([^,}]+),modality=([^}]+)\}$")
_CH = re.compile(r"^chars_total\{modality=([^}]+)\}$")


def run_canaries(engine, cases: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    spec = ((engine.cfg.raw.get("monitoring") or {}).get("canaries") or {})
    cases = cases if cases is not None else spec.get("cases") or []
    failed: list[str] = []
    for i, c in enumerate(cases):
        cid = str(c.get("id", i))
        req = DetectionRequest(request_id=f"canary_{cid}", text=c["text"], language=c.get("language", "en"),
                               modality=c.get("modality", "typed"), normalization_profile=c.get("normalization_profile", "default"),
                               metadata={"canary": True}, options=DetectionOptions(mode=Mode.REDACT))
        resp = engine.redact(req)
        ok = resp.status == "ok"
        out = (resp.output.sanitized_text or "") if ok else ""
        if ok and c.get("must_not_redact"):
            ok = not any(d.decision != DecisionValue.KEEP for d in resp.detections)
        if ok:
            ok = all(v not in out for v in c.get("must_redact", []))
        if ok and c.get("entities"):
            ok = set(c["entities"]) <= {d.entity_type for d in resp.detections if d.decision != DecisionValue.KEEP}
        telemetry.count("canary_pass" if ok else "canary_fail")
        telemetry.event(log, "phi.canary", canary=cid, status="pass" if ok else "fail")
        if not ok:
            failed.append(cid)
    return {"cases": len(cases), "passed": len(cases) - len(failed), "failed": failed, "ok": not failed and bool(cases)}


def live_counts(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    det: dict[str, dict[str, int]] = {}
    chars: dict[str, int] = {}
    for k, v in snapshot.items():
        if m := _DET.match(k):
            det.setdefault(m.group(2), {})[m.group(1)] = int(v)
        elif m := _CH.match(k):
            chars[m.group(1)] = int(v)
    return det, chars


def check_rates(baseline: dict[str, Any], snapshot: dict[str, Any], sigma: float = 3.0,
                min_chars: int = 20_000) -> list[dict[str, Any]]:
    """baseline: {"rates_per_1k_chars": {modality: {entity: rate}}}. Returns alerts (no text, counts only)."""
    det, chars = live_counts(snapshot)
    alerts = []
    for mod, rates in (baseline.get("rates_per_1k_chars") or {}).items():
        n_chars = chars.get(mod, 0)
        if n_chars < min_chars:
            continue                                   # not enough traffic in the window to judge
        for ent, rate in rates.items():
            exp = rate * n_chars / 1000.0
            if exp < 5:                                # Poisson normal approximation needs a few expected events
                continue
            obs = det.get(mod, {}).get(ent, 0)
            z = (obs - exp) / math.sqrt(exp)
            if abs(z) > sigma:
                alerts.append({"modality": mod, "entity": ent, "observed": obs, "expected": round(exp, 1), "z": round(z, 2),
                               "direction": "drop" if z < 0 else "surge"})
                telemetry.count(f"rate_alert{{entity={ent},modality={mod}}}")
    return alerts
