"""RegistryStore: resolves a request's opaque RegistryContext into SCOPED registry data.

A request never carries registry records. It carries references:
  subject_context_id          -> the subject's own record (names, IDs, DOB, contacts, care team)
  provider/facility/staff_directory_version -> a versioned organisational directory
The store returns only what those references point to - never other members' data.

Backends: InMemoryRegistryStore (tests / evaluation, populated by the dataset loader) and
JsonRegistryStore (local files under configs registry.directories_path). A production backend
would implement the same `resolve`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from harness.phi.contracts import RegistryContext


class RegistryAccessError(LookupError):
    pass


class RegistryStore:
    def subject(self, subject_context_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def directory(self, kind: str, version: str) -> list[Any]:
        raise NotImplementedError

    def resolve(self, ctx: Optional[RegistryContext]) -> tuple[Optional[dict[str, Any]], dict[str, str]]:
        """(scoped registry dict in harness.shield.registry format, versions used)."""
        if ctx is None:
            return None, {}
        reg: dict[str, Any] = {}
        versions: dict[str, str] = {}
        if ctx.subject_context_id:
            reg = json.loads(json.dumps(self.subject(ctx.subject_context_id)))   # defensive copy
            versions["subject"] = ctx.subject_context_id
        for kind, ver, key in (("provider_directory", ctx.provider_directory_version, "providers"),
                               ("facility_directory", ctx.facility_directory_version, "facilities"),
                               ("staff_directory", ctx.staff_directory_version, "staff")):
            if ver:
                reg.setdefault(key, [])
                reg[key] = list(reg[key]) + list(self.directory(kind, ver))
                versions[kind] = ver
        if reg.get("simulated_from_gold"):
            raise RegistryAccessError("registry derived from gold labels is refused (evaluation leakage)")
        return (reg or None), versions


class InMemoryRegistryStore(RegistryStore):
    def __init__(self) -> None:
        self._subjects: dict[str, dict[str, Any]] = {}
        self._dirs: dict[tuple[str, str], list[Any]] = {}

    def add_subject(self, subject_context_id: str, record: dict[str, Any]) -> None:
        self._subjects[subject_context_id] = record

    def add_directory(self, kind: str, version: str, entries: list[Any]) -> None:
        self._dirs[(kind, version)] = entries

    def subject(self, subject_context_id: str) -> dict[str, Any]:
        if subject_context_id not in self._subjects:
            raise RegistryAccessError(f"unknown subject_context_id {subject_context_id!r}")
        return self._subjects[subject_context_id]

    def directory(self, kind: str, version: str) -> list[Any]:
        if (kind, version) not in self._dirs:
            raise RegistryAccessError(f"unknown {kind} version {version!r}")
        return self._dirs[(kind, version)]


class JsonRegistryStore(InMemoryRegistryStore):
    """<root>/subjects/<subject_context_id>.json and <root>/<kind>/<version>.json (local only)."""

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self.root = Path(root)

    def subject(self, subject_context_id: str) -> dict[str, Any]:
        if subject_context_id in self._subjects:
            return self._subjects[subject_context_id]
        p = self.root / "subjects" / f"{Path(subject_context_id).name}.json"
        if not p.exists():
            raise RegistryAccessError(f"unknown subject_context_id {subject_context_id!r}")
        return json.loads(p.read_text())

    def directory(self, kind: str, version: str) -> list[Any]:
        if (kind, version) in self._dirs:
            return self._dirs[(kind, version)]
        p = self.root / kind / f"{Path(version).name}.json"
        if not p.exists():
            raise RegistryAccessError(f"unknown {kind} version {version!r}")
        return json.loads(p.read_text())
