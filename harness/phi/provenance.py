"""Provenance stamps for every response and report: code commit / fingerprint, config hash,
catalog version, model revisions and weight hashes (AWS returns ModelVersion on every DetectPHI
response; we go further and stamp the exact code + config + weights)."""
from __future__ import annotations

import functools
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Optional

from harness.paths import ROOT

RUNTIME_SOURCES = ["harness/phi", "harness/shield", "harness/adapters", "harness/taxonomy.py", "harness/schema.py",
                   "harness/label_mapping.py"]


@functools.lru_cache(maxsize=1)
def code_commit() -> str:
    """HEAD commit (with '+dirty' when the work tree differs), or 'uncommitted' without git history."""
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        if not head:
            return "uncommitted"
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *RUNTIME_SOURCES],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return head[:12] + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """SHA-256 over the runtime source tree (stable with or without git)."""
    h = hashlib.sha256()
    for rel in RUNTIME_SOURCES:
        p = ROOT / rel
        files = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(ROOT)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def weights_sha256(adapter: Any) -> Optional[str]:
    """SHA-256 over the model's local weight files for the pinned revision (no network)."""
    try:
        from huggingface_hub import scan_cache_dir
        rev = adapter.entry.get("revision")
        for repo in scan_cache_dir().repos:
            if repo.repo_id != adapter.entry.get("model_id"):
                continue
            for r in repo.revisions:
                if r.commit_hash == rev:
                    h = hashlib.sha256()
                    for f in sorted(r.files, key=lambda x: x.file_name):
                        if f.file_name.endswith((".safetensors", ".bin", ".pt", ".onnx")):
                            with open(f.file_path, "rb") as fh:
                                for chunk in iter(lambda: fh.read(1 << 20), b""):
                                    h.update(chunk)
                    return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None
    return None
