"""Prompt suite (T2): curated English prompts with expected PHI inventories, run through the SAME engine.

  python -m harness_eval.suite validate --suite eval_suites/english_v1       # resolve anchors, lint, freeze
  python -m harness_eval.suite run      --suite eval_suites/english_v1 [--group C_conversational] --output DIR
  python -m harness_eval.suite baseline --suite eval_suites/english_v1 --run DIR --update   # reviewed baseline
  python -m harness_eval.suite regress  --suite eval_suites/english_v1 [--run DIR]          # exit 1 on regression
  # one baseline per tier: baseline.json (contextual model on) and baseline_fast.json (--model none)
  python -m harness_eval.suite regress  --model none --baseline baseline_fast.json
  python -m harness_eval.suite compare  RUN_A RUN_B                                         # outcome diff

Answers, per prompt and per entity: which PHI was present, what should have been detected / redacted,
what was detected, what was redacted, what was missed and WHY (failure reason + failed stage), which
recognizer / model was responsible (raw score, threshold and its source, decision), and how much PHI
remained after redaction. Holdout prompts (holdout: true) are scored but their per-prompt details are
hidden from reports unless --show-holdout, so the suite does not quietly become a tuning set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from harness.paths import ROOT
from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionOptions, DetectionRequest, GoldDocument, GoldEntity, Mode, stable_id
from harness.phi.engine import PHIEngine
from harness.phi.registry_store import JsonRegistryStore
from harness_eval.scoring import Bundle, breakdown, csv_rows, run_policy, score

log = logging.getLogger("harness_eval.suite")
GROUPS = ["A_single_entity", "B_multi_entity_clinical", "C_conversational", "D_asr_style", "E_contextual",
          "F_registry_scoped", "G_edge_case", "N_negative"]
SPOKEN = {"telephone", "asr", "dictation", "face_to_face"}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expected(_M):
    entity_type: str
    subtype: Optional[str] = None
    match: str
    occurrence: int = 1
    expected_action: Literal["REDACT", "KEEP"] = "REDACT"
    expected_sources: list[str] = Field(default_factory=list)


class Lost(_M):
    entity_type: str
    source_match: str


class AsrInfo(_M):
    source_text: str
    lost: list[Lost] = Field(default_factory=list)


class Prompt(_M):
    prompt_id: str
    group: Literal["A_single_entity", "B_multi_entity_clinical", "C_conversational", "D_asr_style", "E_contextual",
                   "F_registry_scoped", "G_edge_case", "N_negative"]
    modality: Literal["typed", "note", "dictation", "telephone", "asr"]
    holdout: bool = False
    text: str
    registry: Optional[dict[str, str]] = None
    expected: list[Expected] = Field(default_factory=list)
    must_not_redact: list[str] = Field(default_factory=list)
    asr: Optional[AsrInfo] = None
    status: Literal["expected_pass", "known_failure", "exploratory"] = "expected_pass"
    notes: str = ""


def _find(text: str, sub: str, occurrence: int) -> Optional[tuple[int, int]]:
    i = -1
    for _ in range(occurrence):
        i = text.find(sub, i + 1)
        if i < 0:
            return None
    return i, i + len(sub)


# ---------------------------------------------------------------------------- validate
def validate(suite: Path, cfg: HarnessConfig) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    cat = Catalog(cfg)
    parent = cat.subtype_parent()
    rows, errors, warnings = [], [], []
    files = sorted((suite / "parts").glob("*.jsonl")) or [suite / "prompts.jsonl"]
    seen = set()
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{f.name}:{n}"
            try:
                p = Prompt.model_validate_json(line)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{where}: invalid prompt: {str(exc).splitlines()[0]}")
                continue
            if p.prompt_id in seen:
                errors.append(f"{where}: duplicate prompt_id {p.prompt_id}")
            seen.add(p.prompt_id)
            if p.group == "N_negative" and p.expected:
                errors.append(f"{p.prompt_id}: negative prompt has expected PHI")
            if p.group == "N_negative" and not p.must_not_redact:
                errors.append(f"{p.prompt_id}: negative prompt needs must_not_redact anchors")
            if p.registry and not (suite / "registry" / "subjects" / f"{p.registry.get('subject_context_id')}.json").exists():
                errors.append(f"{p.prompt_id}: registry subject {p.registry.get('subject_context_id')} missing")
            spans = []
            for e in p.expected:
                if e.entity_type not in cat:
                    errors.append(f"{p.prompt_id}: unknown entity_type {e.entity_type}")
                if e.subtype and e.subtype != e.entity_type and parent.get(e.subtype) not in (e.entity_type, None):
                    warnings.append(f"{p.prompt_id}: subtype {e.subtype} belongs to {parent.get(e.subtype)}, not {e.entity_type}")
                if e.subtype and e.subtype != e.entity_type and e.subtype not in parent:
                    warnings.append(f"{p.prompt_id}: subtype {e.subtype} not in catalog subtypes of {e.entity_type}")
                sp = _find(p.text, e.match, e.occurrence)
                if sp is None:
                    errors.append(f"{p.prompt_id}: match {e.match!r} occurrence {e.occurrence} not found in text")
                    continue
                for s0, e0, m0 in spans:
                    if sp[0] < e0 and s0 < sp[1]:
                        errors.append(f"{p.prompt_id}: expected spans overlap ({m0!r} / {e.match!r})")
                spans.append((sp[0], sp[1], e.match))
            mnr = []
            for a in p.must_not_redact:
                sp = _find(p.text, a, 1)
                if sp is None:
                    errors.append(f"{p.prompt_id}: must_not_redact {a!r} not found")
                    continue
                if any(sp[0] < e0 and s0 < sp[1] for s0, e0, _ in spans):
                    errors.append(f"{p.prompt_id}: must_not_redact {a!r} overlaps an expected PHI span")
                mnr.append([sp[0], sp[1], a])
            if p.asr:
                for lo in p.asr.lost:
                    if lo.source_match not in p.asr.source_text:
                        errors.append(f"{p.prompt_id}: asr.lost {lo.source_match!r} not in source_text")
            d = p.model_dump()
            d["resolved"] = [{"start": s0, "end": e0} for s0, e0, _ in spans] if len(spans) == len(p.expected) else None
            d["must_not_redact_spans"] = mnr
            rows.append(d)
    hold = sum(r["holdout"] for r in rows)
    if rows and not 0.15 <= hold / len(rows) <= 0.35:
        warnings.append(f"holdout fraction {hold / len(rows):.2f} outside 0.15-0.35")
    return sorted(rows, key=lambda r: r["prompt_id"]), errors, warnings


def write_resolved(suite: Path, rows: list[dict[str, Any]]) -> str:
    p = suite / "prompts.resolved.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_resolved(suite: Path) -> list[dict[str, Any]]:
    p = suite / "prompts.resolved.jsonl"
    if not p.exists():
        raise SystemExit(f"{p} missing - run `python -m harness_eval.suite validate --suite {suite}` first")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------- run
def build_bundle(suite: Path, rows: list[dict[str, Any]], cfg: HarnessConfig, device: str) -> Bundle:
    store = JsonRegistryStore(suite / "registry")
    eng = PHIEngine(cfg, device=device, registry_store=store, self_test=False)
    reqs, golds, col = [], {}, {}
    for r in rows:
        if r.get("resolved") is None:
            continue
        rid = stable_id("req", "suite", r["prompt_id"])
        req = DetectionRequest(request_id=rid, text=r["text"], language="en", modality=r["modality"], source="evaluation_suite",
                               normalization_profile="asr" if r["modality"] in SPOKEN else "default",
                               registry_context=r.get("registry"), policy_profile="evaluation",
                               options=DetectionOptions(mode=Mode.SCORE), metadata={"doc_id": r["prompt_id"]})
        ents = [GoldEntity(annotation_id=f"{r['prompt_id']}#{i}", entity_type=e["entity_type"], subtype=e.get("subtype"),
                           expected_sources=e.get("expected_sources") or [], start=sp["start"], end=sp["end"], source="suite",
                           edge_policy="exact")
                for i, (e, sp) in enumerate(zip(r["expected"], r["resolved"])) if e.get("expected_action", "REDACT") == "REDACT"]
        lost = [{"entity_type": lo["entity_type"], "kind": "lost"} for lo in ((r.get("asr") or {}).get("lost") or [])]
        golds[rid] = GoldDocument(request_id=rid, dataset="suite", entities=ents, asr_lost=lost,
                                  metadata={"doc_id": r["prompt_id"], "language": "en", "doc_type": r["group"],
                                            "group": r["group"], "holdout": r["holdout"], "status": r["status"],
                                            "must_not_redact": r.get("must_not_redact_spans") or []})
        reqs.append(req)
        col[rid] = eng.collect(req, include_disabled=True)
    return Bundle("english_v1_suite", "realistic", reqs, golds, col, eng, {"id": "english_v1_suite", "role": "suite",
                                                                              "phi_class": "fabricated"})


def _coarse(g: str) -> str:
    return "MODEL" if g.startswith("MODEL:") else g


def prompt_records(b: Bundle, res: dict[str, Any], cfg: HarnessConfig) -> list[dict[str, Any]]:
    by_req = defaultdict(list)
    for o in res["outcomes"]:
        by_req[o.annotation_id.split("#")[0]].append(o)
    fp_by = defaultdict(list)
    for f in res["fp_rows"]:
        fp_by[f["doc"]].append(f)
    doc_rows = {d["doc"]: d for d in res["document_rows"]}
    out = []
    for req in b.requests:
        g = b.golds[req.request_id]
        pid = g.metadata["doc_id"]
        ann, dets = run_policy(b, req, cfg)
        ev = {e.evidence_id: e for e in ann}
        gold_by_id = {x.annotation_id: x for x in g.entities}
        ents = []
        for o in by_req.get(pid, []):
            ge = gold_by_id.get(o.annotation_id)
            span = (o.start, o.end) if o.start is not None else None
            hits = [d for d in dets if span and d.original_start < span[1] and span[0] < d.original_end]
            chain = []
            for d in hits:
                for eid in d.contributing_evidence_ids:
                    e = ev.get(eid)
                    if e:
                        chain.append({"recognizer": e.recognizer_id, "entity": e.entity_type, "raw_score": round(e.raw_score, 4),
                                      "score_type": str(getattr(e.score_type, "value", e.score_type)),
                                      "threshold": e.threshold_used, "threshold_source": e.threshold_source,
                                      "view": e.text_view_id})
            actual = sorted({_coarse(s) for s in o.sources})
            expected = sorted(set(ge.expected_sources)) if ge else []
            ents.append({"annotation_id": o.annotation_id, "entity_type": o.entity_type, "subtype": o.subtype,
                         "span": span, "text": req.text[o.start:o.end] if span else None,
                         "expected_action": "REDACT", "detection_status": o.detection_status, "redaction_status": o.redaction_status,
                         "outcome": o.outcome, "failure_reason": None if o.outcome.startswith("detected") else o.outcome,
                         "failed_stage": o.failed_stage, "residual_chars": o.residual_chars,
                         "final_decision": [str(getattr(d.decision, "value", d.decision)) for d in hits],
                         "decision_reason": [d.decision_reason for d in hits], "evidence": chain,
                         "best_rejected": o.best_rejected, "sources": o.sources, "sole_catch": o.sole_catch,
                         "expected_sources": expected,
                         "missed_expected_sources": sorted(set(expected) - set(actual)) if o.outcome.startswith("detected") else expected})
        d = doc_rows.get(pid, {})
        out.append({"prompt_id": pid, "group": g.metadata["group"], "holdout": g.metadata["holdout"],
                    "status": g.metadata["status"], "input_text": req.text,
                    "expected_entities": len([e for e in ents if e["span"] is not None]),
                    "detected": sum(1 for e in ents if e["detection_status"] == "detected_full"),
                    "partially_detected": sum(1 for e in ents if e["detection_status"] == "detected_partial"),
                    "redacted": sum(1 for e in ents if e["redaction_status"] == "redacted_ok"),
                    "missed": [e for e in ents if not e["outcome"].startswith("detected")],
                    "unexpected": fp_by.get(pid, []), "entities": ents,
                    "residual_chars": d.get("residual_chars"), "core_chars": d.get("core_chars"),
                    "over_redacted_chars": d.get("over_redacted_chars"),
                    "must_not_redact_violations": d.get("redaction_must_not_redact_violations", 0),
                    "pass": all(e["outcome"].startswith("detected") for e in ents) and not fp_by.get(pid)
                            and not d.get("redaction_must_not_redact_violations")})
    return out


def _agg(records: list[dict], key) -> dict[str, dict[str, Any]]:
    agg = defaultdict(lambda: {"expected": 0, "detected": 0, "redacted": 0, "missed": 0, "residual_chars": 0})
    for r in records:
        for e in r["entities"]:
            if e["span"] is None:
                continue
            a = agg[key(r, e)]
            a["expected"] += 1
            a["detected"] += e["detection_status"] == "detected_full"
            a["redacted"] += e["redaction_status"] == "redacted_ok"
            a["missed"] += not e["outcome"].startswith("detected")
            a["residual_chars"] += e["residual_chars"]
    for a in agg.values():
        a["recall_full"] = a["redacted"] / a["expected"] if a["expected"] else None
    return dict(sorted(agg.items()))


def summarize(records: list[dict], res: dict[str, Any]) -> dict[str, Any]:
    dev = [r for r in records if not r["holdout"]]
    hold = [r for r in records if r["holdout"]]
    fr = defaultdict(int)
    stage = defaultdict(int)
    for r in records:
        for e in r["entities"]:
            if not e["outcome"].startswith("detected"):
                fr[e["outcome"]] += 1
                stage[e["failed_stage"]] += 1
    by_method = defaultdict(lambda: {"contributed": 0, "sole_catch": 0, "false_positives": 0})
    for r in records:
        for e in r["entities"]:
            for s in e["sources"]:
                by_method[s]["contributed"] += 1
            if e["sole_catch"]:
                by_method[e["sole_catch"]]["sole_catch"] += 1
        for f in r["unexpected"]:
            for s in (f.get("source_groups") or "").split("+"):
                if s:
                    by_method[s]["false_positives"] += 1
    s = res["summary"]
    total_exp = sum(r["expected_entities"] for r in records)
    return {"prompts": len(records), "prompts_holdout": len(hold), "prompts_passed": sum(r["pass"] for r in records),
            "expected_entities": total_exp, "detected": sum(r["detected"] for r in records),
            "redacted": sum(r["redacted"] for r in records), "missed": sum(len(r["missed"]) for r in records),
            "false_positives": sum(len(r["unexpected"]) for r in records),
            "negative_prompts_with_redaction": sum(1 for r in records if r["group"] == "N_negative" and r["unexpected"]),
            "must_not_redact_violations": sum(r["must_not_redact_violations"] or 0 for r in records),
            "residual_phi_chars": sum(r["residual_chars"] or 0 for r in records),
            "expected_phi_chars": sum(r["core_chars"] or 0 for r in records),
            "residual_phi_pct": (sum(r["residual_chars"] or 0 for r in records) / max(1, sum(r["core_chars"] or 0 for r in records))),
            "over_redacted_chars": sum(r["over_redacted_chars"] or 0 for r in records),
            "by_failure_reason": dict(fr), "by_failed_stage": dict(stage), "by_detection_method": dict(by_method),
            "by_entity": _agg(records, lambda r, e: e["entity_type"]),
            "by_subtype": _agg(records, lambda r, e: f"{e['entity_type']}/{e['subtype'] or e['entity_type']}"),
            "by_group": _agg(records, lambda r, e: r["group"]),
            "development_view": {"prompts": len(dev), "by_group": _agg(dev, lambda r, e: r["group"])},
            "holdout_view": {"prompts": len(hold), "recall_full": _agg(hold, lambda r, e: "all").get("all", {}).get("recall_full")},
            "engine_summary": {k: v for k, v in s.items() if k not in ("strata",)}}


def write_run(out: Path, records: list[dict], summ: dict, res: dict, cfg: HarnessConfig, show_holdout: bool, b: Bundle) -> None:
    from harness_eval import ledger
    from harness_eval.report import write_suite_report
    out.mkdir(parents=True, exist_ok=True)
    visible = [r for r in records if show_holdout or not r["holdout"]]
    with open(out / "prompt_records.jsonl", "w", encoding="utf-8") as f:
        for r in visible:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with open(out / "outcomes_all.jsonl", "w", encoding="utf-8") as f:   # all prompts incl. holdout, no text (for baselines)
        for r in records:
            for e in r["entities"]:
                f.write(json.dumps({"prompt_id": r["prompt_id"], "holdout": r["holdout"], "status": r["status"],
                                    "annotation_id": e["annotation_id"], "entity_type": e["entity_type"], "outcome": e["outcome"],
                                    "failed_stage": e["failed_stage"]}) + "\n")
            f.write(json.dumps({"prompt_id": r["prompt_id"], "holdout": r["holdout"], "fp": len(r["unexpected"]),
                                "mnr": r["must_not_redact_violations"], "residual_chars": r["residual_chars"]}) + "\n")
    csv_rows(out / "entity_evaluations.csv", [{"prompt_id": r["prompt_id"], "group": r["group"], **{k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                                                for k, v in e.items() if k != "evidence"}} for r in visible for e in r["entities"]])
    csv_rows(out / "unexpected.csv", [{"prompt_id": r["prompt_id"], **f} for r in visible for f in r["unexpected"]])
    csv_rows(out / "residual.csv", [x for x in res["residual_rows"] if show_holdout or not b.golds[x["request_id"]].metadata["holdout"]])
    for k in ("by_entity", "by_subtype", "by_group"):
        csv_rows(out / f"{k}.csv", [{"key": a, **v} for a, v in summ[k].items()])
    csv_rows(out / "by_recognizer.csv", [{"source_group": a, **v} for a, v in summ["by_detection_method"].items()])
    csv_rows(out / "failure_reasons.csv", [{"failure_reason": a, "count": v} for a, v in summ["by_failure_reason"].items()])
    (out / "summary.json").write_text(json.dumps({"suite": "english_v1", "generated": datetime.now().isoformat(timespec="seconds"),
                                                  "config": cfg.describe(), **summ}, indent=1, default=str))
    write_suite_report(out, visible, summ, cfg, hidden_holdout=not show_holdout)
    ledger.record("suite run", cfg, [b.manifest], {"prompts": summ["prompts"], "missed": summ["missed"],
                                                    "false_positives": summ["false_positives"],
                                                    "residual_phi_pct": round(summ["residual_phi_pct"], 4)}, str(out))


# ---------------------------------------------------------------------------- baseline / regress / compare
def _load_outcomes(run: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    ents, prompts = {}, {}
    for line in (run / "outcomes_all.jsonl").read_text().splitlines():
        r = json.loads(line)
        if "annotation_id" in r:
            ents[r["annotation_id"]] = r
        else:
            prompts[r["prompt_id"]] = r
    return ents, prompts


def make_baseline(suite: Path, run: Path, name: str = "baseline.json") -> Path:
    ents, prompts = _load_outcomes(run)
    summ = json.loads((run / "summary.json").read_text())
    base = {"created": datetime.now().isoformat(timespec="seconds"), "from_run": str(run),
            "config_sha256": summ["config"]["config_sha256"], "suite_sha256": hashlib.sha256((suite / "prompts.resolved.jsonl").read_bytes()).hexdigest(),
            "entities": {k: {"success": v["outcome"].startswith("detected"), "outcome": v["outcome"]} for k, v in ents.items()},
            "prompts": prompts, "residual_phi_pct": summ["residual_phi_pct"]}
    p = suite / name
    p.write_text(json.dumps(base, indent=1, sort_keys=True))
    return p


def regress(suite: Path, run: Path, tolerance_pct: float = 0.002, name: str = "baseline.json") -> tuple[list[str], list[str]]:
    base = json.loads((suite / name).read_text())
    ents, prompts = _load_outcomes(run)
    summ = json.loads((run / "summary.json").read_text())
    regressions, improvements = [], []
    for aid, b in base["entities"].items():
        now = ents.get(aid)
        if now is None:
            regressions.append(f"{aid}: missing from run")
            continue
        ok = now["outcome"].startswith("detected")
        if b["success"] and not ok:
            regressions.append(f"{aid} ({now['entity_type']}): was protected, now {now['outcome']} [{now['failed_stage']}]")
        elif not b["success"] and ok:
            improvements.append(f"{aid} ({now['entity_type']}): now protected (was {b['outcome']})")
    for pid, b in base["prompts"].items():
        now = prompts.get(pid, {})
        if now.get("fp", 0) > b.get("fp", 0):
            regressions.append(f"{pid}: false positives {b.get('fp', 0)} -> {now.get('fp')}")
        if (now.get("mnr") or 0) > (b.get("mnr") or 0):
            regressions.append(f"{pid}: must_not_redact violations {b.get('mnr')} -> {now.get('mnr')}")
    if summ["residual_phi_pct"] > base["residual_phi_pct"] + tolerance_pct:
        regressions.append(f"residual PHI {base['residual_phi_pct']:.4f} -> {summ['residual_phi_pct']:.4f}")
    return regressions, improvements


def run_suite(suite: Path, cfg: HarnessConfig, device: str, out: Path, groups: Optional[list[str]] = None,
              show_holdout: bool = False) -> dict[str, Any]:
    rows = load_resolved(suite)
    if groups:
        rows = [r for r in rows if r["group"] in groups]
    b = build_bundle(suite, rows, cfg, device)
    res = score(b, cfg)
    records = prompt_records(b, res, cfg)
    summ = summarize(records, res)
    write_run(out, records, summ, res, cfg, show_holdout, b)
    return summ


def main(argv=None) -> int:
    from harness.cli.common import setup_logging
    ap = argparse.ArgumentParser(prog="python -m harness_eval.suite")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("validate", "run", "baseline", "regress"):
        s = sub.add_parser(c)
        s.add_argument("--suite", default="eval_suites/english_v1")
        s.add_argument("--config", default=None)
        s.add_argument("--env", default=None)
        s.add_argument("--device", default="auto")
        if c in ("run", "baseline", "regress"):
            s.add_argument("--model", default=None, help="comma list or 'none' (e.g. fast tier without weights)")
        if c == "run":
            s.add_argument("--group", action="append", default=None)
            s.add_argument("--output", default=None)
            s.add_argument("--show-holdout", action="store_true")
        if c in ("baseline", "regress"):
            s.add_argument("--run", default=None, help="existing run dir (default: run the suite now)")
            s.add_argument("--baseline", default="baseline.json", help="baseline file in the suite dir (one per tier)")
            s.add_argument("--update", action="store_true")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("run_a")
    cmp_.add_argument("run_b")
    a = ap.parse_args(argv)
    setup_logging()
    if a.cmd == "compare":
        ea, _ = _load_outcomes(Path(a.run_a))
        eb, _ = _load_outcomes(Path(a.run_b))
        for k in sorted(set(ea) | set(eb)):
            oa, ob = (ea.get(k) or {}).get("outcome"), (eb.get(k) or {}).get("outcome")
            if oa != ob:
                print(f"{k:<14} {str(oa):<30} -> {ob}")
        return 0
    suite = ROOT / a.suite if not Path(a.suite).is_absolute() else Path(a.suite)
    cfg = HarnessConfig.load(a.config, env=a.env)
    from harness.devices import detect_device
    if a.cmd == "validate":
        rows, errors, warnings = validate(suite, cfg)
        for w in warnings:
            print("WARN ", w)
        for e in errors:
            print("ERROR", e)
        if errors:
            print(f"{len(errors)} error(s); prompts.resolved.jsonl NOT written")
            return 1
        sha = write_resolved(suite, rows)
        print(f"OK: {len(rows)} prompts ({sum(r['holdout'] for r in rows)} holdout), "
              f"{sum(len(r['expected']) for r in rows)} expected entities; prompts.resolved.jsonl sha256={sha[:16]}")
        print("If this is a reviewed change, re-sign the manifest: "
              "python -m harness_eval.manifests sign english_v1_suite --reason '...'")
        return 0
    if getattr(a, "model", None):
        cfg.only_models([] if a.model == "none" else a.model.split(","))
    if a.cmd == "run":
        out = Path(a.output) if a.output else ROOT / "results" / "suite" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
        from harness_eval import manifests
        manifests.verify("english_v1_suite")
        s = run_suite(suite, cfg, detect_device(a.device), out, a.group, a.show_holdout)
        print(f"{s['prompts']} prompts ({s['prompts_holdout']} holdout): expected {s['expected_entities']}, detected {s['detected']}, "
              f"redacted {s['redacted']}, missed {s['missed']}, false positives {s['false_positives']}, residual PHI "
              f"{s['residual_phi_pct'] * 100:.1f}% -> {out}")
        return 0
    run = Path(a.run) if a.run else None
    if run is None:
        run = ROOT / "results" / "suite" / f"regress_{datetime.now():%Y-%m-%d_%H%M%S}"
        run_suite(suite, cfg, detect_device(a.device), run)
    if a.cmd == "baseline":
        if not a.update:
            print(f"dry run: pass --update to write {a.baseline} from", run)
            return 0
        print("baseline written:", make_baseline(suite, run, a.baseline))
        return 0
    regs, imps = regress(suite, run, name=a.baseline)
    for i in imps:
        print("IMPROVED  ", i)
    for r in regs:
        print("REGRESSION", r)
    print(f"{len(regs)} regression(s), {len(imps)} improvement(s)")
    return 1 if regs else 0


if __name__ == "__main__":
    sys.exit(main())
