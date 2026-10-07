"""Behavioural (CheckList) tests for the PHI engine - T1.

  python -m harness_eval.checklist run [--tier fast|model|all] [--enable ...] [--output DIR]

MFT  every value in every template must be FULLY redacted (all characters of the value hidden)
INV  the MFT outcome must not change under lowercase, punctuation loss, fillers, wrapping, chunk-boundary
     placement (fast tier) and name-origin swaps (model tier)
DIR  context-required patterns: not redacted without the cue, redacted with it; clinical / non-PHI
     sentences must produce no redaction at all
Runs through the public engine API (engine.redact) - the same path as production. Gate G5: MFT pass rate
100% for every ENABLED entity in the tier.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.config import load_yaml
from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode, stable_id
from harness.phi.engine import PHIEngine

TEMPLATES = ROOT / "eval_suites" / "checklist_v1" / "templates.yaml"
FILLER = ("The member was contacted for routine care coordination and the plan of care was reviewed. ")


@dataclass
class Case:
    test_type: str          # MFT | SPOKEN_MFT | INV:<name> | INV:name_origin | DIR:context_without | DIR:context_with | DIR:negative
    entity: str
    tier: str
    text: str
    span: Optional[tuple[int, int]]
    expect_redacted: bool
    profile: str = "default"
    modality: str = "typed"


def _apply(op: dict, text: str, span: tuple[int, int]) -> tuple[str, tuple[int, int]]:
    s, e = span
    v = text[s:e]
    if op["op"] == "lower":
        return text.lower(), span
    if op["op"] == "strip_punct":
        t = re.sub(r"[.,;:!?]+$", "", text)
        return t, span
    if op["op"] == "prefix_value":
        p = op["arg"]
        return text[:s] + p + text[s:], (s + len(p), e + len(p))
    if op["op"] == "wrap_value":
        a, b = op["arg"]
        return text[:s] + a + v + b + text[e:], (s + len(a), e + len(a))
    if op["op"] == "pad_before":
        # whole sentences only: truncating mid-word would glue the filler onto the cue ("...and tMRN") and
        # test a malformed input instead of chunk placement
        pad = FILLER * -(-int(op["arg"]) // len(FILLER))
        return pad + text, (s + len(pad), e + len(pad))
    raise ValueError(op)


def generate(tiers: set[str]) -> list[Case]:
    t = load_yaml(str(TEMPLATES))
    cases: list[Case] = []
    for section, ttype, profile, modality in (("mft", "MFT", "default", "typed"), ("spoken_mft", "SPOKEN_MFT", "asr", "telephone")):
        for ent, spec in (t.get(section) or {}).items():
            if spec["tier"] not in tiers:
                continue
            for tpl in spec["templates"]:
                for v in spec["values"]:
                    text = tpl.replace("{v}", v)
                    s = tpl.index("{v}")
                    base = Case(ttype, ent.split("@")[0], spec["tier"], text, (s, s + len(v)), True, profile, modality)
                    cases.append(base)
                    if section == "mft" and spec["tier"] == "fast":
                        for op in [o for o in t.get("inv") or [] if o["name"] not in set(spec.get("skip_inv", []))]:
                            nt, ns = _apply(op, text, base.span)
                            cases.append(Case(f"INV:{op['name']}", ent, "fast", nt, ns, True, profile, modality))
    if "model" in tiers:
        names = ((t.get("mft") or {}).get("NAME") or {}).get("values", [])
        for tpl in (t.get("name_origin_inv") or {}).get("templates", []):
            for v in names:
                s = tpl.index("{v}")
                cases.append(Case("INV:name_origin", "NAME", "model", tpl.replace("{v}", v), (s, s + len(v)), True))
    d = t.get("dir") or {}
    if "fast" in tiers:
        for c in d.get("context_required", []):
            i = c["without"].index(c["value"])
            cases.append(Case("DIR:context_without", c["entity"], "fast", c["without"], (i, i + len(c["value"])), False))
            j = c["with"].index(c["value"])
            cases.append(Case("DIR:context_with", c["entity"], "fast", c["with"], (j, j + len(c["value"])), True))
        for sentence in d.get("must_not_redact", []):
            cases.append(Case("DIR:negative", "NONE", "fast", sentence, None, False))
    return cases


def run_cases(engine: PHIEngine, cases: list[Case]) -> list[dict[str, Any]]:
    out = []
    for i, c in enumerate(cases):
        req = DetectionRequest(request_id=stable_id("req", "checklist", i, c.text), text=c.text, language="en",
                               modality=c.modality, normalization_profile=c.profile,
                               options=DetectionOptions(mode=Mode.REDACT))
        resp = engine.redact(req)
        hidden: set[int] = set()
        for d in resp.detections:
            if str(getattr(d.decision, "value", d.decision)) in ("REDACT", "REVIEW"):
                hidden.update(range(d.original_start, d.original_end))
        if c.span is None:
            ok = not hidden and resp.status == "ok"
            got = "no redaction" if ok else f"redacted {len(hidden)} chars"
        else:
            core = {k for k in range(*c.span) if c.text[k].isalnum()}
            covered = core <= hidden
            ok = (covered if c.expect_redacted else not (core & hidden)) and resp.status == "ok"
            got = "fully redacted" if covered else ("partially redacted" if core & hidden else "not redacted")
        types = sorted({d.entity_type for d in resp.detections if d.original_start < (c.span or (0, 0))[1]
                        and (c.span or (0, 0))[0] < d.original_end})
        type_ok = None
        if c.span is not None and c.expect_redacted:      # dominant type over the value's characters
            owner = {}
            for d in resp.detections:
                if str(getattr(d.decision, "value", d.decision)) in ("REDACT", "REVIEW"):
                    for k in range(d.original_start, d.original_end):
                        owner[k] = d.entity_type
            hits = [owner[k] for k in range(*c.span) if c.text[k].isalnum() and k in owner]
            type_ok = bool(hits) and max(set(hits), key=hits.count) == c.entity
        out.append({"test_type": c.test_type, "entity": c.entity, "tier": c.tier, "pass": ok, "got": got,
                    "expected": "redacted" if c.expect_redacted else "not redacted", "detected_types": types,
                    "type_ok": type_ok, "status": resp.status, "text": c.text})
    return out


def summarize(rows: list[dict[str, Any]], cfg: HarnessConfig) -> dict[str, Any]:
    agg = defaultdict(lambda: {"cases": 0, "passed": 0})
    for r in rows:
        a = agg[(r["entity"], r["test_type"].split(":")[0] if r["test_type"].startswith("INV") else r["test_type"])]
        a["cases"] += 1
        a["passed"] += r["pass"]
    by = {f"{e}|{t}": {**v, "pass_rate": v["passed"] / v["cases"]} for (e, t), v in sorted(agg.items())}
    enabled = set(cfg.enabled_entities)
    mft = [r for r in rows if r["test_type"] in ("MFT", "SPOKEN_MFT") and r["entity"] in enabled]
    typed = [r for r in mft if r.get("type_ok") is not None]
    return {"cases": len(rows), "passed": sum(r["pass"] for r in rows),
            "mft_enabled_entities_pass_rate": (sum(r["pass"] for r in mft) / len(mft)) if mft else None,
            "mft_type_accuracy": (sum(r["type_ok"] for r in typed) / len(typed)) if typed else None,
            "type_errors": sorted({f"{r['entity']} -> {'/'.join(r['detected_types'])}" for r in typed if not r["type_ok"]}),
            "gate_G5_mft_100pct": bool(mft) and all(r["pass"] for r in mft), "by_entity_test": by}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.checklist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tier", choices=["fast", "model", "all"], default="fast")
    r.add_argument("--config", default=None)
    r.add_argument("--env", default=None)
    r.add_argument("--enable", default="", help="comma list of recognizers / entities to enable for this run")
    r.add_argument("--device", default="auto")
    r.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    cfg = HarnessConfig.load(a.config, env=a.env)
    for n in [x.strip() for x in a.enable.split(",") if x.strip()]:
        if n in cfg.raw["recognizers"]:
            cfg.set_recognizers([n], True)
        else:
            cfg.set_entities([n], True)
    tiers = {"fast", "model"} if a.tier == "all" else {a.tier}
    if a.tier == "fast":
        cfg.only_models([])
    from harness.devices import detect_device
    eng = PHIEngine(cfg, device=detect_device(a.device), self_test=False)
    rows = run_cases(eng, generate(tiers))
    s = summarize(rows, cfg)
    out = Path(a.output) if a.output else ROOT / "results" / "checklist" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    (out / "summary.json").write_text(json.dumps({"config": cfg.describe(), **s}, indent=1, default=str))
    fails = [x for x in rows if not x["pass"]]
    for f in fails[:40]:
        print(f"FAIL {f['test_type']:<22} {f['entity']:<16} expected {f['expected']:<13} got {f['got']:<18} {f['text'][:80]!r}")
    print(f"MFT type accuracy {s['mft_type_accuracy']}; type errors: {s['type_errors'][:12]}")
    print(f"{s['passed']}/{s['cases']} passed; MFT pass rate (enabled entities) {s['mft_enabled_entities_pass_rate']}; "
          f"G5 {'PASS' if s['gate_G5_mft_100pct'] else 'FAIL'} -> {out}")
    return 0 if s["gate_G5_mft_100pct"] else 1


if __name__ == "__main__":
    sys.exit(main())
