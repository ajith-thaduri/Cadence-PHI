"""Load a v2 experiment directory into one dict."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.config import default_model_keys, model_entry, models_registry


def _read(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def load_run(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    m = run_dir / "metrics"
    out: dict[str, Any] = {"run_dir": str(run_dir), "config": _read(run_dir / "config.json"),
                           "datasets": _read(m / "_datasets.json") or {}, "baselines": _read(m / "_shield_baselines.json") or {},
                           "models": {}, "latency": {}, "latency_cpu": {}, "excluded": {}}
    for key in default_model_keys():
        out["models"][key] = _read(m / f"{key}.json") or {"model": key, "status": "NOT EVALUATED",
                                                          "metadata": {"display_name": model_entry(key)["display_name"]}}
        out["latency"][key] = _read(run_dir / "latency" / f"{key}.json")
        out["latency_cpu"][key] = _read(run_dir / "latency" / f"{key}__cpu.json")
    for key, e in models_registry().items():
        if e.get("evaluation_role") == "excluded":
            out["excluded"][key] = e
    return out


def display_name(key: str) -> str:
    return model_entry(key)["display_name"]
