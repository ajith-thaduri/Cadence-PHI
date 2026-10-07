"""python -m harness.phi.config {validate|show|migrate}

  validate [--config ROOT] [--env prod]   resolve + strict-validate; print resolved hash and sources (exit 1 on error)
  show     [--config ROOT] [--env prod]   print the resolved configuration as JSON (no secrets are stored in config)
  migrate  --from configs/phi_harness.yaml --to configs/phi   split a legacy single file into the layered layout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from harness.phi.config import ConfigError, HarnessConfig

LAYOUT = {   # file -> top-level keys it owns
    "entities.yaml": ["catalog_version", "entities", "aliases"],
    "recognizers/rules.yaml": [("recognizers", "family")],
    "recognizers/contextual.yaml": [("recognizers", "contextual"), "contextual_params"],
    "recognizers/registry.yaml": [("recognizers", "kinds"), "registry"],
    "recognizers/external.yaml": [("recognizers", "presidio_class")],
    "recognizers/patterns.yaml": [("recognizers", "pattern")],
    "recognizers/exclusions.yaml": ["allow_list"],
    "models.yaml": ["models"],
    "normalization.yaml": ["normalizers", "normalized_views_feed", "normalization_profiles"],
    "reconciliation.yaml": ["precedence", "type_refinements"],
    "policy/thresholds.yaml": ["thresholds"],
    "policy/profiles.yaml": ["policy_profiles"],
    "policy/redaction.yaml": ["redaction"],
    "policy/failure.yaml": ["failure"],
    "policy/outbound_guard.yaml": ["outbound_guard"],
    "runtime.yaml": ["presidio", "output", "telemetry"],
}
ROOT_KEYS = ["version", "engine", "languages"]


def _kind(spec: dict) -> str:
    if spec.get("type") == "pattern":
        return "pattern"
    if spec.get("contextual"):
        return "contextual"
    if "kinds" in spec:
        return "kinds"
    if "presidio_class" in spec:
        return "presidio_class"
    return "family"


def migrate(src: str, dst: str) -> list[Path]:
    from harness.config import load_yaml
    raw = load_yaml(src)
    out = Path(dst)
    written = []
    for fname, keys in LAYOUT.items():
        data: dict = {}
        for k in keys:
            if isinstance(k, tuple):
                sec, kind = k
                sel = {rid: spec for rid, spec in (raw.get(sec) or {}).items() if _kind(spec) == kind}
                if sel:
                    data.setdefault(sec, {}).update(sel)
            elif k in raw:
                data[k] = raw[k]
        p = out / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# migrated from {src}\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        written.append(p)
    root = {k: raw[k] for k in ROOT_KEYS if k in raw}
    root.update({"default_environment": "base", "environments": "environments", "includes": list(LAYOUT),
                 "generated": ["generated/thresholds_calibrated.yaml"]})
    (out / "phi.yaml").write_text("# composition root\n" + yaml.safe_dump(root, sort_keys=False, allow_unicode=True))
    for env in ("base", "dev", "test", "prod"):
        (out / "environments").mkdir(exist_ok=True)
        p = out / "environments" / f"{env}.yaml"
        if not p.exists():
            p.write_text(f"# {env} environment overlay (allow-listed keys only)\n{{}}\n")
    if raw.get("presets"):
        ev = Path("configs/eval/presets.yaml")
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text("# ablation presets (evaluation only; never loaded by the engine)\n" +
                      yaml.safe_dump({"presets": raw["presets"]}, sort_keys=False))
    return written + [out / "phi.yaml"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.phi.config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("validate", "show"):
        s = sub.add_parser(c)
        s.add_argument("--config", default=None)
        s.add_argument("--env", default=None)
        s.add_argument("--no-generated", action="store_true")
    m = sub.add_parser("migrate")
    m.add_argument("--from", dest="src", default="configs/phi_harness.yaml")
    m.add_argument("--to", dest="dst", default="configs/phi")
    a = ap.parse_args(argv)
    if a.cmd == "migrate":
        for p in migrate(a.src, a.dst):
            print("wrote", p)
        return 0
    try:
        cfg = HarnessConfig.load(a.config, env=a.env, apply_generated=False if a.no_generated else None)
    except ConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    if a.cmd == "validate":
        d = cfg.describe()
        print(json.dumps({"valid": True, "config_sha256": d["config_sha256"], "environment": d["environment"],
                          "catalog_version": d["catalog_version"], "sources": d["sources"],
                          "generated_overlays": (cfg.raw.get("_source") or {}).get("generated", []),
                          "models_enabled": d["models_enabled"], "entities_enabled": len(d["entities_enabled"])}, indent=1))
    else:
        print(json.dumps({k: v for k, v in cfg.raw.items() if not k.startswith("_")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
