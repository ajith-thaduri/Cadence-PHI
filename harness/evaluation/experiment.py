"""Timestamped experiment directories."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.devices import hardware_info, library_versions
from harness.env import offline_mode_enabled
from harness.paths import RESULTS

SUBDIRS = ["raw", "predictions", "metrics", "false_negatives", "false_positives", "latency",
           "reports", "plots", "models"]


def new_run_dir(root: Path = RESULTS) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    d = root / stamp
    for sub in SUBDIRS:
        (d / sub).mkdir(parents=True, exist_ok=True)
    (root / "LATEST").write_text(stamp)
    return d


def latest_run_dir(root: Path = RESULTS) -> Optional[Path]:
    p = root / "LATEST"
    if p.exists() and (root / p.read_text().strip()).exists():
        return root / p.read_text().strip()
    runs = sorted(x for x in root.glob("20*") if x.is_dir())
    return runs[-1] if runs else None


def write_run_info(run_dir: Path, args: dict[str, Any], device: str, datasets: dict[str, Any]) -> None:
    info = {"argv": sys.argv, "args": args, "device": device, "offline_mode": offline_mode_enabled(),
            "hardware": hardware_info(), "libraries": library_versions(), "datasets": datasets,
            "started": datetime.now().isoformat(timespec="seconds")}
    (run_dir / "config.json").write_text(json.dumps(info, indent=2, default=str))
