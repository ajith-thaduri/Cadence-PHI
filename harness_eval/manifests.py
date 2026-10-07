"""Dataset manifests: roles, hashes, provenance (configs/eval/datasets.yaml).

  python -m harness_eval.manifests verify            # every registered file matches its signed sha256
  python -m harness_eval.manifests sign <id> --reason "..."   # re-sign after a REVIEWED data change (logged)

Rules enforced by the evaluation tooling:
  - a dataset whose file hash differs from the manifest cannot be evaluated (changed data = new version)
  - calibration and rule development may only read datasets whose role is `dev`
  - every evaluation of a `test` or `release` dataset is recorded in the evaluation ledger
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from harness.config import load_yaml
from harness.paths import ROOT

MANIFEST = ROOT / "configs" / "eval" / "datasets.yaml"


class ManifestError(ValueError):
    pass


def load() -> dict[str, Any]:
    if not MANIFEST.exists():
        raise ManifestError(f"dataset manifest missing: {MANIFEST}")
    return load_yaml(str(MANIFEST)) or {}


def entry(ds_id: str) -> dict[str, Any]:
    m = load()
    if ds_id not in (m.get("datasets") or {}):
        raise ManifestError(f"dataset {ds_id!r} is not registered in configs/eval/datasets.yaml")
    return m["datasets"][ds_id]


def role(ds_id: str) -> str:
    m = load()
    if ds_id in (m.get("groups") or {}):
        roles = {entry(x)["role"] for x in m["groups"][ds_id]}
        return roles.pop() if len(roles) == 1 else "mixed"
    return entry(ds_id)["role"]


def expand(names: list[str]) -> list[str]:
    m = load()
    out: list[str] = []
    for n in names:
        out += (m.get("groups") or {}).get(n, [n])
    return list(dict.fromkeys(out))


def file_sha(path: str | Path) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def verify(ds_id: str) -> dict[str, Any]:
    e = entry(ds_id)
    p = ROOT / e["path"]
    if not p.exists():
        raise ManifestError(f"{ds_id}: file {e['path']} missing")
    actual = file_sha(e["path"])
    if e.get("sha256") and actual != e["sha256"]:
        raise ManifestError(f"{ds_id}: file hash {actual[:12]} != signed manifest hash {e['sha256'][:12]} - the data changed; "
                            "review and re-sign with `python -m harness_eval.manifests sign`")
    return {"id": ds_id, "role": e["role"], "phi_class": e.get("phi_class"), "sha256": actual}


def require_role(ds_ids: list[str], allowed: set[str], purpose: str) -> None:
    bad = [d for d in ds_ids if role(d) not in allowed]
    if bad:
        raise ManifestError(f"{purpose} refused for datasets {bad}: role must be one of {sorted(allowed)}")


def sign(ds_id: str, reason: str) -> str:
    m = load()
    e = m["datasets"][ds_id]
    new = file_sha(e["path"])
    old = e.get("sha256")
    e["sha256"] = new
    e.setdefault("history", []).append({"date": datetime.now().isoformat(timespec="seconds"), "old": old, "new": new,
                                        "reason": reason})
    head = MANIFEST.read_text().split("manifest_version")[0]
    MANIFEST.write_text(head + yaml.safe_dump(m, sort_keys=False, width=140))
    return new


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.manifests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify")
    s = sub.add_parser("sign")
    s.add_argument("dataset")
    s.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "sign":
        print(a.dataset, sign(a.dataset, a.reason)[:16])
        return 0
    ok = True
    for ds in (load().get("datasets") or {}):
        try:
            v = verify(ds)
            print(f"OK    {ds:<26} role={v['role']:<6} sha={v['sha256'][:12]}")
        except ManifestError as exc:
            ok = False
            print(f"FAIL  {exc}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
