"""System card and datasheets, GENERATED from the live configuration, manifests and ledger (plan §17).

  python -m harness_eval.cards system-card  [--env prod]      -> docs/system_card.md
  python -m harness_eval.cards datasheets                     -> docs/datasheets/<dataset>.md
  python -m harness_eval.cards all

Generated, not hand-written, so the card cannot drift from what is deployed: every number and setting below is
read from configs/phi (resolved for the chosen environment), configs/models.yaml, configs/eval/*.yaml,
results/ledger.jsonl and the newest gate / perf / checklist / suite results. Regenerate on every release.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.config import load_yaml
from harness.paths import ROOT
from harness.phi import provenance
from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness_eval import manifests

DOCS = ROOT / "docs"


def _newest(pattern: str) -> Optional[Path]:
    c = sorted(ROOT.glob(pattern), key=lambda p: p.stat().st_mtime)
    return c[-1] if c else None


def _ledger_tail(n: int = 400) -> list[dict[str, Any]]:
    p = ROOT / "results" / "ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines()[-n:] if x.strip()]


def system_card(env: Optional[str]) -> str:
    cfg = HarnessConfig.load(None, env=env)
    cat = Catalog(cfg)
    models = load_yaml(str(ROOT / "configs" / "models.yaml")).get("models", {})
    d = cfg.describe()
    L = [f"# PHI detection & redaction engine - system card", "",
         f"_Generated {datetime.now():%Y-%m-%d %H:%M} by `python -m harness_eval.cards system-card` for environment "
         f"`{d['environment']}`. Do not edit by hand - regenerate._", "",
         "| item | value |", "|---|---|",
         f"| config sha256 | `{d['config_sha256']}` |", f"| catalog version | `{d['catalog_version']}` |",
         f"| code commit | `{provenance.code_commit()}` |", f"| code fingerprint | `{provenance.code_fingerprint()}` |",
         f"| threshold overlays | {', '.join(Path(x).name for x in d['threshold_overlays']) or 'none'} |", "",
         "## Intended use", "",
         "Detect and redact (tokenize) PHI in English member-call transcripts, care notes and dictations before the text "
         "crosses a trust boundary (e.g. an LLM call). Tokens are reversible only through the vault, inside the boundary. "
         "Out of scope: languages other than English (requests are rejected), images/PDF, de-identification claims under "
         "Expert Determination, any use without the outbound guard in front of external services.", "",
         "## Pipeline", "",
         "normalization views -> deterministic rules / declarative patterns / conversational rules / scoped registry / "
         "contextual model (evidence only) -> policy thresholds (dev-calibrated) -> reconciliation (non-overlapping, "
         "precedence, type refinement) -> redaction (Presidio AnonymizerEngine) -> vault -> outbound guard (independent, "
         "no ML). Fail-closed on every failure class (NIST SP 800-53 SC-24 / SI-17).", "",
         "## Models", "", "| key | enabled | mode | model id | revision | weights sha256 pinned |", "|---|---|---|---|---|---|"]
    for k, s in cfg.raw["models"].items():
        m = models.get(k, {})
        L.append(f"| {k} | {s.get('enabled')} | {s.get('mode', 'production')} | {m.get('model_id', '?')} | "
                 f"`{str(m.get('revision', '?'))[:12]}` | {'yes' if s.get('weights_sha256') else 'no'} |")
    L += ["", "## Entities (catalog)", "", "| entity | enabled | risk tier | Safe Harbor | default action | outbound guard |",
          "|---|---|---|---|---|---|"]
    for e in cfg.entity_ids:
        i = cat.info(e) if hasattr(cat, "info") else None
        spec = cfg.raw["entities"][e]
        L.append(f"| {e} | {spec.get('enabled', True)} | {spec.get('risk_tier', '')} | {spec.get('hipaa_safe_harbor', '')} | "
                 f"{spec.get('default_action', '')} | {spec.get('outbound_guard', 'off')} |")
    rec = cfg.raw["recognizers"]
    L += ["", "## Recognizers", "", f"{sum(cfg.recognizer_enabled(r) for r in rec)} of {len(rec)} enabled; shadow: "
          f"{sorted(cfg.shadow_ids) or 'none'}.", "",
          "| recognizer | enabled | version | developed on |", "|---|---|---|---|"]
    for r, s in rec.items():
        L.append(f"| {r} | {cfg.recognizer_enabled(r)} | {s.get('version', '1')} | {', '.join(s.get('developed_on') or []) or '-'} |")
    red = cfg.raw.get("redaction") or {}
    v = red.get("vault") or {}
    L += ["", "## Redaction and vault", "",
          f"- default operator `{red.get('default_operator')}`, token format `{red.get('token_format')}`, consistency scope "
          f"`{red.get('consistency_scope')}`",
          f"- vault backend `{v.get('backend')}` (TTL {v.get('ttl_days')} days, audit {v.get('audit')}, key `{v.get('key_ref')}`)",
          f"- outbound guard: enabled {cfg.raw.get('outbound_guard', {}).get('enabled')}, action "
          f"`{cfg.raw.get('outbound_guard', {}).get('action')}`, blocking entities {len(cat.outbound_block())}",
          f"- model-hit cache entries retained by a serving engine: {(cfg.raw.get('model_cache') or {}).get('max_entries', 0)}", ""]
    L += ["## Evidence", ""]
    gates = _newest("results/gates/*/gates.json")
    if gates:
        g = json.loads(gates.read_text())
        L += [f"Release gates ({gates.parent.name}): **{g['decision']}** - "
              + ", ".join(f"{k} {v['verdict']}" for k, v in g["gates"].items()), ""]
    else:
        L += ["Release gates: not run (`python -m harness_eval.gates run`).", ""]
    for label, pat in (("CheckList (fast)", "results/checklist/fast_default/summary.json"),
                       ("CheckList (model)", "results/checklist/model_default/summary.json")):
        p = ROOT / pat
        if p.exists():
            s = json.loads(p.read_text())
            L.append(f"- {label}: {s['passed']}/{s['cases']} cases; G5 {'PASS' if s['gate_G5_mft_100pct'] else 'FAIL'}")
    perf = _newest("results/perf/*/perf.json")
    if perf:
        pr = json.loads(perf.read_text())
        lat = (pr.get("latency") or {}).get("<=4000", {}).get("wall_ms", {})
        L.append(f"- Performance ({perf.parent.name}, device {pr.get('device')}): typical call p95 {lat.get('p95')} ms; "
                 f"G8 {pr.get('G8', {}).get('verdict')}")
    evals = [r for r in _ledger_tail() if r.get("command") == "evaluate"]
    if evals:
        last = evals[-1]
        L.append(f"- Latest scored evaluation ({last['run_id']}, test access {last['test_access']}): "
                 + "; ".join(f"{k}: residual {v.get('residual_mean')}, high-risk recall {round(v.get('high_risk_full_recall') or 0, 4)}"
                             for k, v in (last.get("headline") or {}).items()))
    L += ["", "## Known limitations", "",
          "- **No governed real-data (T4) evaluation exists.** All measurements are on fabricated data; gates G1-G3/G6 are "
          "INSUFFICIENT_EVIDENCE and the release decision is NO-GO until T4 evidence is produced under governance.",
          "- ASR input loses entities before detection (`ASR_destroyed`); residual PHI on ASR text is materially higher than on typed text.",
          "- The contextual model's CPU latency on long calls exceeds the proposed SLO; see the performance evidence.",
          "- Gate numbers are PROPOSED (configs/eval/gates.yaml status) until signed by the risk owner.", ""]
    return "\n".join(L)


def datasheet(ds_id: str, entry: dict[str, Any]) -> str:
    L = [f"# Datasheet - `{ds_id}`", "", "_Generated from configs/eval/datasets.yaml (Gebru et al., Datasheets for Datasets)._", "",
         "| field | value |", "|---|---|"]
    for k in ("role", "phi_class", "modality", "language", "path", "sha256", "provenance", "access_class", "license", "retention"):
        if k in entry:
            L.append(f"| {k} | `{entry[k]}` |" if k in ("sha256", "path") else f"| {k} | {entry[k]} |")
    L += ["", "## Use restrictions", "",
          {"dev": "Development only: rule development, calibration and debugging are permitted.",
           "test": "Frozen held-out: measured once per release candidate; every access is ledgered; no tuning, no rule development.",
           "suite": "Regression / manual-evaluation suite: never a tuning set; holdout prompts hidden from debugging views.",
           "release": "Governed real data: access-controlled; outputs never contain span text."}.get(entry.get("role"), "See manifest roles."), ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.cards")
    ap.add_argument("what", choices=["system-card", "datasheets", "all"])
    ap.add_argument("--env", default="prod")
    a = ap.parse_args(argv)
    if a.what in ("system-card", "all"):
        (DOCS / "system_card.md").write_text(system_card(a.env))
        print("wrote", DOCS / "system_card.md")
    if a.what in ("datasheets", "all"):
        out = DOCS / "datasheets"
        out.mkdir(parents=True, exist_ok=True)
        for ds_id, entry in manifests.load()["datasets"].items():
            (out / f"{ds_id}.md").write_text(datasheet(ds_id, entry))
        print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
