"""Console audit trail: who did what, when - ids, counts and keyed hashes only, never text (AU-2 / AU-3)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from harness.phi import telemetry

_SAFE = (str, int, float, bool, type(None))


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, user: str, role: str, action: str, ok: bool = True, **details: Any) -> None:
        clean = {k: v for k, v in details.items()
                 if isinstance(v, _SAFE) or (isinstance(v, dict) and all(isinstance(x, (int, float)) for x in v.values()))}
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "user": user, "role": role, "action": action, "ok": ok, **clean}
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
        telemetry.count(f"ui_action_total{{action={action}}}")

    def tail(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-limit:]
        return [json.loads(x) for x in reversed(lines) if x.strip()]


def text_fingerprint(text: str) -> str:
    return telemetry.keyed_hash(text)
