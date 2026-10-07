"""Compose the layered configuration into one resolved, validated, hashed dict.

Resolution order (each step recorded in raw["_source"]):
  1. composition root (configs/phi/phi.yaml): ordered `includes` - files are deep-merged; the SAME
     leaf key defined in two files is an error (no silent override between owners)
  2. environment overlays: environments/base.yaml, then environments/<env>.yaml. Only allow-listed
     paths may be set (ENV_ALLOWED) - an environment can never change thresholds, entity enablement,
     recognizers or failure actions
  3. generated overlays (e.g. generated/thresholds_calibrated.yaml) when apply_generated is true;
     each must carry _provenance.developed_on listing DEVELOPMENT datasets only
  4. (later, outside the loader) CLI overrides for evaluation and request allow-lists (narrow only)
The loader never falls back to defaults: a missing include or overlay is a ConfigError.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Any, Iterable

from harness.config import load_yaml

ENVIRONMENTS = ("base", "dev", "test", "prod")
ENV_ALLOWED = [
    "environment", "apply_generated",
    "failure.deadline_ms", "failure.deadline_ms.*", "failure.max_input_chars", "failure.startup_self_test.required",
    "output.*", "registry.directories_path", "redaction.vault.*", "models.*.device", "telemetry.*", "model_cache.*", "monitoring.detection_rate.*",
    "monitoring.canaries.interval_seconds",
]
DEV_ROLES_FALLBACK = {"cadence_dev", "cadence_dev_text", "cadence_dev_asr", "synthetic_dev", "meddocan_dev"}


class LoaderError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _leaves(d: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(d, dict) and not d and not prefix:
        return
    if isinstance(d, dict) and d:
        for k, v in d.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    else:
        yield prefix, d


def _merge_strict(dst: dict, src: dict, where: str, owner: dict[str, str], path: str = "") -> None:
    for k, v in src.items():
        p = f"{path}.{k}" if path else str(k)
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_strict(dst[k], v, where, owner, p)
        elif k in dst:
            raise LoaderError(f"config key {p!r} defined in both {owner.get(p, '?')} and {where}")
        else:
            dst[k] = copy.deepcopy(v)
            for leaf, _ in _leaves(v, p):
                owner[leaf] = where
            owner[p] = where


def _merge_over(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge_over(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def _check_env_overlay(name: str, data: dict) -> None:
    for leaf, _ in _leaves(data):
        if not any(fnmatch.fnmatchcase(leaf, pat) or leaf == pat for pat in ENV_ALLOWED):
            raise LoaderError(f"environment overlay {name!r} may not set {leaf!r} (allowed: {ENV_ALLOWED})")


def dev_dataset_ids() -> set[str]:
    """Dataset ids whose manifest role is 'dev' (configs/eval/datasets.yaml); fallback list if absent."""
    from harness.paths import ROOT
    man = ROOT / "configs" / "eval" / "datasets.yaml"
    if man.exists():
        data = load_yaml(str(man)) or {}
        ids = {k for k, v in (data.get("datasets") or {}).items() if (v or {}).get("role") == "dev"}
        groups = {k for k, v in (data.get("groups") or {}).items()
                  if all((data.get("datasets") or {}).get(m, {}).get("role") == "dev" for m in v)}
        return ids | groups
    return set(DEV_ROLES_FALLBACK)


def _apply_generated(raw: dict, path: Path) -> dict:
    data = load_yaml(str(path)) or {}
    prov = data.get("_provenance") or {}
    used = set(prov.get("developed_on") or [])
    if not used:
        raise LoaderError(f"generated overlay {path.name}: missing _provenance.developed_on")
    bad = used - dev_dataset_ids()
    if bad:
        raise LoaderError(f"generated overlay {path.name}: developed on non-development data {sorted(bad)} - refused")
    th = data.get("thresholds") or {}
    unknown = set(th) - {"model", "model_entity", "entity", "modality"}
    if unknown:
        raise LoaderError(f"generated overlay {path.name}: may only set thresholds.model / model_entity / entity / "
                          f"modality, got {sorted(unknown)}")
    _merge_over(raw.setdefault("thresholds", {}), th)
    return {"path": str(path), "sha256": _sha(path), "developed_on": sorted(used), "date": prov.get("date")}


def compose(root_path: str | Path, env: str | None = None, apply_generated: bool | None = None) -> dict[str, Any]:
    root_path = Path(root_path)
    base_dir = root_path.parent
    root = load_yaml(str(root_path)) or {}
    if "includes" not in root:
        raise LoaderError(f"{root_path} is not a composition root (no `includes`)")
    raw: dict[str, Any] = {}
    owner: dict[str, str] = {}
    files = [{"path": str(root_path), "sha256": _sha(root_path)}]
    for k, v in root.items():
        if k not in ("includes", "environments", "generated", "default_environment"):
            _merge_strict(raw, {k: v}, root_path.name, owner)
    for inc in root["includes"]:
        p = base_dir / inc
        if not p.exists():
            raise LoaderError(f"include {inc!r} not found (no silent defaults)")
        _merge_strict(raw, load_yaml(str(p)) or {}, inc, owner)
        files.append({"path": str(p), "sha256": _sha(p)})
    env = env or os.environ.get("PHI_ENV") or root.get("default_environment", "base")
    if env not in ENVIRONMENTS:
        raise LoaderError(f"unknown environment {env!r}; one of {ENVIRONMENTS}")
    env_dir = base_dir / (root.get("environments") or "environments")
    for name in dict.fromkeys(["base", env]):
        p = env_dir / f"{name}.yaml"
        if not p.exists():
            raise LoaderError(f"environment overlay {p} not found")
        data = load_yaml(str(p)) or {}
        _check_env_overlay(name, data)
        _merge_over(raw, data)
        files.append({"path": str(p), "sha256": _sha(p)})
    raw["environment"] = env
    gen_applied = []
    do_gen = raw.get("apply_generated", True) if apply_generated is None else apply_generated
    raw["apply_generated"] = bool(do_gen)
    if do_gen:
        for g in root.get("generated") or []:
            p = base_dir / g
            if p.exists():
                gen_applied.append(_apply_generated(raw, p))
    raw["_source"] = {"root": str(root_path), "files": files, "environment": env, "generated": gen_applied}
    raw["_overlays"] = [g["path"] for g in gen_applied]
    return raw
