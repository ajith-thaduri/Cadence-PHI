"""Manual evaluation: run arbitrary English text through exactly the same PHIEngine the application uses.

  python -m harness_eval.analyze --input sample.txt                                   # one document, printed
  python -m harness_eval.analyze --input sample.txt --redact --output results/example # + sanitized text, report
  python -m harness_eval.analyze --input-dir data/manual_eval --output results/manual_eval
  python -m harness_eval.analyze --input requests.jsonl --output results/batch        # one DetectionRequest per line
  python -m harness_eval.analyze --input-dir data/manual_eval --gold data/manual_eval/gold.jsonl --output results/me_gold

Inputs
  *.txt                one document; doc_id = path relative to --input-dir (or the file stem)
  *.json               one DetectionRequest
  *.jsonl              one DetectionRequest per line
  <stem>.meta.json     optional sidecar for a .txt: {"subject_context_id", "provider_directory_version",
                       "facility_directory_version", "staff_directory_version", "modality", "language",
                       "normalization_profile", "policy_profile", "metadata"}
Gold (optional, never enters the engine): JSONL, one line per document
  {"doc_id": "...", "entities": [{"entity_type": "NAME", "start": 10, "end": 23, "text": "optional check"}],
   "ignore": [{"start": 0, "end": 4}], "asr_lost": [{"entity_type": "PHONE"}]}

Outputs (--output): responses.jsonl, detections.csv, evidence.csv, redacted/*.txt (with --redact),
report.md, report.html, summary.json; with --gold also entity_metrics.csv, residual_phi.csv,
false_negatives.csv, false_positives.csv, attribution.csv, gold_summary.json.
These files contain document text (that is their purpose) - keep them local. Logs never do.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.paths import ROOT
from harness.phi.config import HarnessConfig, UnsupportedLanguageError
from harness.phi.contracts import (DecisionValue, DetectionOptions, DetectionRequest, DetectionResponse, GoldDocument,
                                   GoldEntity, Mode, Operator, RedactionRequest, RedactionResponse, stable_id)
from harness.phi.engine import Collected, PHIEngine

log = logging.getLogger("harness_eval.analyze")
META_KEYS = {"subject_context_id", "provider_directory_version", "facility_directory_version", "staff_directory_version"}


@dataclass
class Doc:
    doc_id: str
    request: DetectionRequest
    source: str


@dataclass
class Result:
    doc: Doc
    response: DetectionResponse
    collected: Collected
    redaction: Optional[RedactionResponse] = None
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------- inputs
def _request_from_text(doc_id: str, text: str, meta: dict[str, Any], args) -> DetectionRequest:
    ctx = {k: meta[k] for k in META_KEYS if meta.get(k)}
    if not ctx.get("subject_context_id") and getattr(args, "subject", None):
        ctx["subject_context_id"] = args.subject
    return DetectionRequest(
        request_id=stable_id("req", "manual", doc_id), text=text, language=meta.get("language", args.language),
        modality=meta.get("modality", args.modality), source=meta.get("source", "manual_evaluation"),
        normalization_profile=meta.get("normalization_profile", args.normalization_profile),
        policy_profile=meta.get("policy_profile", args.policy_profile), registry_context=ctx or None,
        metadata={**(meta.get("metadata") or {}), "doc_id": doc_id}, options=DetectionOptions(mode=Mode.DEBUG, include_text=True))


def _debug(req: DetectionRequest) -> DetectionRequest:
    return req.model_copy(update={"options": DetectionOptions(mode=Mode.DEBUG, include_text=True)})


def collect_inputs(args) -> list[Doc]:
    files: list[tuple[str, Path]] = []
    for p in args.input or []:
        files.append((Path(p).stem, Path(p)))
    if args.input_dir:
        root = Path(args.input_dir)
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if "registry" in rel.parts[:-1]:
                continue          # registry store files live beside the inputs, never treated as documents
            if p.is_file() and p.suffix in (".txt", ".json", ".jsonl") and not p.name.endswith((".meta.json", ".gold.jsonl")) \
                    and p.name not in ("gold.jsonl",):
                files.append((str(p.relative_to(root).with_suffix("")), p))
    docs: list[Doc] = []
    for doc_id, p in files:
        if not p.exists():
            raise SystemExit(f"input not found: {p}")
        if p.suffix == ".txt":
            meta_p = p.with_name(p.stem + ".meta.json")
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            docs.append(Doc(doc_id, _request_from_text(doc_id, p.read_text(encoding="utf-8"), meta, args), str(p)))
        elif p.suffix == ".json":
            r = DetectionRequest.model_validate_json(p.read_text())
            docs.append(Doc(str(r.metadata.get("doc_id", doc_id)), _debug(r), str(p)))
        else:
            for i, line in enumerate(p.read_text().splitlines()):
                if line.strip():
                    r = DetectionRequest.model_validate_json(line)
                    docs.append(Doc(str(r.metadata.get("doc_id", r.request_id)), _debug(r), f"{p}:{i + 1}"))
    if not docs:
        raise SystemExit("no input documents (use --input FILE or --input-dir DIR)")
    ids = [d.doc_id for d in docs]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate doc_id values in the input; give each document a unique name / metadata.doc_id")
    return docs


def read_gold(path: Path, docs: list[Doc], edge_policy: str) -> dict[str, GoldDocument]:
    """Simple manual gold format -> GoldDocument (evaluation layer only)."""
    from harness_eval.datasets import trim_edges
    from harness.taxonomy import CANONICAL_LABELS
    by_id = {d.doc_id: d for d in docs}
    out: dict[str, GoldDocument] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        g = json.loads(line)
        d = by_id.get(str(g["doc_id"]))
        if d is None:
            log.warning("gold line %d: doc_id %r not among the inputs - skipped", n, g["doc_id"])
            continue
        text = d.request.text
        ents = []
        for i, e in enumerate(g.get("entities", [])):
            if e["entity_type"] not in CANONICAL_LABELS:
                raise SystemExit(f"gold line {n}: unknown entity_type {e['entity_type']!r}; use one of {CANONICAL_LABELS}")
            s, t = int(e["start"]), int(e["end"])
            if not (0 <= s < t <= len(text)):
                raise SystemExit(f"gold line {n}: span [{s},{t}) outside document {g['doc_id']!r}")
            if "text" in e and text[s:t] != e["text"]:
                raise SystemExit(f"gold line {n}: span [{s},{t}) is {text[s:t]!r}, gold says {e['text']!r}")
            a, b = trim_edges(text, s, t) if edge_policy == "trim_edge_function_words" else (s, t)
            ents.append(GoldEntity(annotation_id=f"{g['doc_id']}#{i}", entity_type=e["entity_type"], start=a if b > a else s,
                                   end=b if b > a else t, source="manual", edge_policy=edge_policy))
        for j, ig in enumerate(g.get("ignore", [])):
            ents.append(GoldEntity(annotation_id=f"{g['doc_id']}#ign{j}", entity_type="IGNORE", start=int(ig["start"]),
                                   end=int(ig["end"]), source="manual", ignore_region=True))
        lost = [{"entity_type": x["entity_type"], "kind": x.get("kind", "deleted")} for x in g.get("asr_lost", [])]
        out[d.request.request_id] = GoldDocument(request_id=d.request.request_id, dataset="manual", entities=ents, asr_lost=lost,
                                                 metadata={"doc_id": d.doc_id, "language": d.request.language,
                                                           "doc_type": d.request.modality, "data_source": "manual"})
    return out


# ---------------------------------------------------------------------------- run
def run(engine: PHIEngine, docs: list[Doc], redact: bool, operator: Optional[str]) -> list[Result]:
    """Every document goes through the SAME engine calls an application uses: analyze, then
    (optionally) redact(RedactionRequest). Evidence is collected once per document; the superset
    collection only adds rejected 'disabled' evidence for failure attribution - decisions are
    identical to engine.analyze(request)."""
    results = []
    for d in docs:
        try:
            col = engine.collect(d.request, include_disabled=True)
        except UnsupportedLanguageError:
            log.error("document skipped: unsupported language (doc index %d)", len(results))
            continue
        resp = engine.analyze(d.request, collected=col)
        if resp.status == "blocked":
            log.error("document blocked by fail-closed policy: %s", resp.failure.code if resp.failure else "?")
        rr = None
        if redact:
            pol = engine.cfg.policy(d.request.policy_profile)
            rr = engine.redact(RedactionRequest(request_id=d.request.request_id, original_text=d.request.text,
                                                detections=resp.detections,
                                                operator=Operator(operator or pol.get("operator", "TOKENIZE")),
                                                redact_review=pol.get("redact_review", True)))
        results.append(Result(d, resp, col, rr))
    return results


# ---------------------------------------------------------------------------- outputs
def _ev_index(resp: DetectionResponse) -> dict[str, Any]:
    return {e.evidence_id: e for e in resp.evidence}


def detection_rows(results: list[Result], show: bool) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        ev = _ev_index(r.response)
        for d in r.response.detections:
            chain = [ev[i] for i in d.contributing_evidence_ids if i in ev]
            rows.append({"doc_id": r.doc.doc_id, "start": d.original_start, "end": d.original_end,
                         "text": (d.text if show else f"<{d.original_end - d.original_start} chars>"),
                         "entity_type": d.entity_type, "decision": d.decision, "confidence_class": d.confidence_class,
                         "decision_score": d.decision_score, "threshold": d.threshold,
                         "recognizers": "+".join(d.contributing_recognizers),
                         "evidence": "; ".join(f"{e.recognizer_id}:{e.entity_type}:{e.raw_score:.3f}:{e.score_type}"
                                               f"(thr {e.threshold_used} {e.threshold_source})" for e in chain),
                         "rejected": "; ".join(f"{x.recognizer_id}:{x.entity_type}:{x.raw_score:.3f}:{x.reason}"
                                               for x in d.rejected_evidence),
                         "reason": d.decision_reason})
    return rows


def evidence_rows(results: list[Result], show: bool) -> list[dict[str, Any]]:
    rows = []
    for r in results:
        for e in r.response.evidence:
            rows.append({"doc_id": r.doc.doc_id, "evidence_id": e.evidence_id, "recognizer_id": e.recognizer_id,
                         "source": e.source, "entity_type": e.entity_type, "start": e.original_start, "end": e.original_end,
                         "text": e.text if show else f"<{e.original_end - e.original_start} chars>",
                         "raw_score": round(e.raw_score, 4), "score_type": e.score_type, "model_id": e.model_id,
                         "model_revision": e.model_revision, "native_label": e.metadata.get("native_label"),
                         "rule_id": e.rule_id, "view": e.text_view_id,
                         "transformations": "+".join(e.metadata.get("transformations") or []),
                         "threshold_used": e.threshold_used, "threshold_source": e.threshold_source,
                         "accepted": e.accepted_as_candidate, "rejection_reason": e.rejection_reason})
    return rows


def _csv(path: Path, rows: list[dict]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r)) if rows else ["empty"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def marked_text(text: str, dets, show: bool, as_html: bool) -> str:
    """Original text with detections marked: ⟦TYPE: span⟧ (markdown) or <mark> (html)."""
    out, pos = [], 0
    for d in sorted(dets, key=lambda d: d.original_start):
        if d.original_start < pos:
            continue
        seg = text[pos:d.original_start]
        out.append(html.escape(seg) if as_html else seg)
        span = text[d.original_start:d.original_end] if show else "█" * (d.original_end - d.original_start)
        if as_html:
            cls = {"REDACT": "r", "REVIEW": "v", "KEEP": "k"}[d.decision]
            tip = html.escape(f"{d.entity_type} {d.decision} {d.decision_score} {d.decision_reason}")
            out.append(f"<mark class={cls} title=\"{tip}\">{html.escape(span)}<sub>{d.entity_type}</sub></mark>")
        else:
            out.append(f"⟦{d.entity_type}{'' if d.decision == 'REDACT' else ' ' + d.decision}: {span}⟧")
        pos = d.original_end
    out.append(html.escape(text[pos:]) if as_html else text[pos:])
    return "".join(out)


CSS = """:root{--bg:#fff;--fg:#1d2330;--mute:#5b6475;--line:#dfe3ea;--hd:#f4f6f9;--r:#ffe08a;--v:#ffc4c4;--k:#e4e7ec}
@media (prefers-color-scheme: dark){:root{--bg:#14171c;--fg:#e6e9ef;--mute:#9aa3b2;--line:#2c323c;--hd:#1c2027;--r:#6b5600;--v:#6b2a2a;--k:#333a45}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,sans-serif;max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:21px}h2{font-size:17px;margin-top:30px;border-bottom:1px solid var(--line)}h3{font-size:14px}
pre{white-space:pre-wrap;background:var(--hd);padding:10px;border-radius:6px;font-size:13px}
table{border-collapse:collapse;width:100%;font-size:12.5px;display:block;overflow-x:auto;margin:6px 0}
th,td{border:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}th{background:var(--hd)}
mark.r{background:var(--r)}mark.v{background:var(--v)}mark.k{background:var(--k)}mark{color:inherit;border-radius:3px;padding:0 2px}
mark sub{font-size:9px;opacity:.75;margin-left:2px}.note{color:var(--mute);font-size:12.5px}"""


def _tbl_md(head, rows):
    esc = lambda c: str(c).replace("|", "\\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows])


def _tbl_html(head, rows):
    return ("<table><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr>" +
            "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


def write_report(out: Path, results: list[Result], cfg: HarnessConfig, show: bool, gold_res: Optional[dict]) -> None:
    det_head = ["span", "text", "entity", "decision", "conf.", "score", "threshold", "recognizers / evidence", "reason"]
    md = ["# PHI manual evaluation report", "",
          f"Generated {datetime.now():%Y-%m-%d %H:%M}. Config `{cfg.path}` (sha {cfg.sha256()}); models: "
          f"{', '.join(cfg.enabled_models) or 'none'}; threshold overlays: {cfg.raw.get('_overlays') or 'none'}.",
          "Scores are raw recognizer scores in their own scale (score_type) - not probabilities.",
          "**This report contains document text. Keep it local.**", ""]
    h = [f"<h1>PHI manual evaluation report</h1><p class=note>{html.escape(md[2])}<br>{html.escape(md[3])}<br><b>"
         "This report contains document text. Keep it local.</b> Legend: <mark class=r>REDACT</mark> "
         "<mark class=v>REVIEW</mark> <mark class=k>KEEP (below threshold)</mark></p>"]
    tot = {"docs": len(results), "detections": 0, "ms": 0.0}
    for r in results:
        resp = r.response
        ev = _ev_index(resp)
        text = r.doc.request.text
        tot["detections"] += resp.summary.entities_detected
        tot["ms"] += resp.timings.total_ms
        head = (f"{len(text)} chars · language {resp.input.language} · modality {resp.input.modality} · normalization "
                f"{resp.normalization.profile} ({', '.join(resp.normalization.normalizers_applied) or 'none'}) · "
                f"{resp.summary.entities_detected} redacted spans {resp.summary.entities_by_type} · candidates "
                f"{resp.summary.candidates} (accepted {resp.summary.candidates_accepted}) · {resp.timings.total_ms:.0f} ms "
                f"(models {resp.timings.model_ms:.0f} ms)")
        rows = []
        for d in resp.detections:
            chain = [ev[i] for i in d.contributing_evidence_ids if i in ev]
            evs = ", ".join(f"{e.recognizer_id} {e.raw_score:.3f} {e.score_type}" for e in chain) or \
                  ", ".join(f"{x.recognizer_id} {x.raw_score:.3f} ({x.reason})" for x in d.rejected_evidence)
            rows.append([f"{d.original_start}-{d.original_end}", d.text if show else "█" * min(20, d.original_end - d.original_start),
                         d.entity_type, d.decision, d.confidence_class, d.decision_score, d.threshold, evs, d.decision_reason])
        md += [f"## {r.doc.doc_id}", "", f"_{head}_", "", "**Marked text** (⟦TYPE: span⟧ = redacted)", "",
               "```text", marked_text(text, resp.detections, show, False), "```", "", "**Detections**", "",
               _tbl_md(det_head, rows) if rows else "_none_", ""]
        h += [f"<h2>{html.escape(r.doc.doc_id)}</h2><p class=note>{html.escape(head)}</p>",
              f"<pre>{marked_text(text, resp.detections, show, True)}</pre>", "<h3>Detections</h3>",
              _tbl_html(det_head, rows) if rows else "<p>none</p>"]
        if r.redaction is not None:
            md += ["**Redacted text**", "", "```text", r.redaction.sanitized_text, "```", ""]
            h += ["<h3>Redacted text</h3>", f"<pre>{html.escape(r.redaction.sanitized_text)}</pre>"]
        if gold_res and r.doc.request.request_id in gold_res["per_doc"]:
            miss = gold_res["per_doc"][r.doc.request.request_id]
            mrows = [[f"{o.start}-{o.end}" if o.start is not None else "–",
                      (text[o.start:o.end] if show and o.start is not None else "–"), o.entity_type, o.outcome,
                      f"{o.residual_chars}/{o.core_chars}",
                      "" if not o.best_rejected else f"{o.best_rejected['recognizer']} {o.best_rejected['raw_score']} "
                                                     f"thr {o.best_rejected['threshold']} ({o.best_rejected['reason']})"]
                     for o in miss if not o.outcome.startswith("detected")]
            mhead = ["gold span", "text", "entity", "why missed", "leaked/core chars", "best rejected evidence"]
            md += ["**Gold PHI not fully redacted (why)**", "", _tbl_md(mhead, mrows) if mrows else "_none - every gold entity fully redacted_", ""]
            h += ["<h3>Gold PHI not fully redacted (why)</h3>", _tbl_html(mhead, mrows) if mrows else "<p>none</p>"]
    summ = f"{tot['docs']} documents · {tot['detections']} redacted spans · {tot['ms']:.0f} ms total"
    md.insert(5, f"**Run:** {summ}\n")
    h.insert(1, f"<p><b>Run:</b> {html.escape(summ)}</p>")
    if gold_res:
        s = gold_res["summary"]
        rr = s["RESIDUAL_PHI_AFTER_SHIELD"]
        g = (f"Gold: mean residual PHI {rr['mean']*100:.1f}% · corpus residual {rr['corpus_residual_char_rate']*100:.1f}% · "
             f"entity full recall {(s['entity_full_recall'] or 0)*100:.1f}% · high-risk full recall {(s['high_risk_full_recall'] or 0)*100:.1f}% · "
             f"span precision {(s['span_precision'] or 0)*100:.1f}% · spurious {s['spurious_spans']} · over-redaction "
             f"{s['over_redaction_char_rate']*100:.2f}% · failure attribution {gold_res['attribution']}")
        md.insert(6, f"**{g}**\n")
        h.insert(2, f"<p><b>{html.escape(g)}</b></p>")
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")
    (out / "report.html").write_text(f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport "
                                     f"content='width=device-width,initial-scale=1'><title>PHI manual evaluation</title>"
                                     f"<style>{CSS}</style></head><body>{''.join(h)}</body></html>", encoding="utf-8")


def score_gold(engine: PHIEngine, results: list[Result], golds: dict[str, GoldDocument], cfg: HarnessConfig) -> dict:
    from harness_eval.scoring import Bundle, breakdown, score
    with_gold = [r for r in results if r.doc.request.request_id in golds]
    b = Bundle("manual", "manual", [r.doc.request for r in with_gold], golds,
               {r.doc.request.request_id: r.collected for r in with_gold}, engine)
    res = score(b, cfg)
    per_doc = {rec.request_id: rec.outcomes for rec in res["records"]}
    return {"result": res, "summary": res["summary"], "per_doc": per_doc, "attribution": breakdown(res)["attribution"].get("*ALL*", {})}


def write_outputs(out: Path, results: list[Result], cfg: HarnessConfig, show: bool, gold: Optional[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "responses.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            resp = r.response if show else r.response.model_copy(update={
                "detections": [d.model_copy(update={"text": None}) for d in r.response.detections],
                "evidence": [e.model_copy(update={"text": None}) for e in r.response.evidence]})
            f.write(json.dumps({"doc_id": r.doc.doc_id, "response": json.loads(resp.model_dump_json())}, ensure_ascii=False) + "\n")
    _csv(out / "detections.csv", detection_rows(results, show))
    _csv(out / "evidence.csv", evidence_rows(results, show))
    if any(r.redaction for r in results):
        (out / "redacted").mkdir(exist_ok=True)
        for r in results:
            if r.redaction:
                (out / "redacted" / (r.doc.doc_id.replace("/", "__") + ".txt")).write_text(r.redaction.sanitized_text, encoding="utf-8")
    summary = {"generated": datetime.now().isoformat(timespec="seconds"), "config": cfg.describe(), "documents": len(results),
               "per_document": [{"doc_id": r.doc.doc_id, "source": r.doc.source, "request_id": r.doc.request.request_id,
                                 "text_sha256": r.response.input.text_sha256, "detections": r.response.summary.entities_detected,
                                 "by_type": r.response.summary.entities_by_type, "candidates": r.response.summary.candidates,
                                 "total_ms": r.response.timings.total_ms, "vault_id": r.redaction.vault_id if r.redaction else None}
                                for r in results]}
    if gold:
        from harness_eval.scoring import csv_rows, thresholds_table
        res = gold["result"]
        csv_rows(out / "entity_metrics.csv", res["entity_rows"])
        csv_rows(out / "residual_phi.csv", res["residual_rows"])
        csv_rows(out / "false_negatives.csv", [x for x in res["residual_rows"] if x["outcome"] != "partial_span"])
        csv_rows(out / "false_positives.csv", res["fp_rows"])
        csv_rows(out / "thresholds.csv", thresholds_table(cfg, [res]))
        csv_rows(out / "attribution.csv", [{"outcome": k, "count": v} for k, v in gold["attribution"].items()])
        (out / "gold_summary.json").write_text(json.dumps({"summary": gold["summary"], "attribution": gold["attribution"]},
                                                          indent=1, default=str))
        summary["gold"] = {"documents_with_gold": len(gold["per_doc"]), **{k: v for k, v in gold["summary"].items()}}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    write_report(out, results, cfg, show, gold)


def print_console(results: list[Result], show: bool, gold: Optional[dict]) -> None:
    for r in results:
        resp = r.response
        print(f"\n=== {r.doc.doc_id}  ({len(r.doc.request.text)} chars, {resp.timings.total_ms:.0f} ms, "
              f"{resp.summary.entities_detected} redacted, {resp.summary.candidates} candidates)")
        ev = _ev_index(resp)
        for d in resp.detections:
            t = d.text if show else f"<{d.original_end - d.original_start} chars>"
            print(f"  {d.decision:<6} {d.entity_type:<16} [{d.original_start}:{d.original_end}] {t!r:<40} "
                  f"{d.confidence_class:<6} score {d.decision_score} thr {d.threshold}  {d.decision_reason}")
            for i in d.contributing_evidence_ids:
                e = ev.get(i)
                if e:
                    print(f"           + {e.recognizer_id:<22} {e.entity_type:<16} {e.raw_score:.3f} {e.score_type:<20} "
                          f"thr {e.threshold_used} ({e.threshold_source})")
            for x in d.rejected_evidence:
                print(f"           - {x.recognizer_id:<22} {x.entity_type:<16} {x.raw_score:.3f} {x.reason}")
        if r.redaction:
            print("\n  redacted:\n  " + r.redaction.sanitized_text.replace("\n", "\n  "))
        if gold and r.doc.request.request_id in gold["per_doc"]:
            for o in gold["per_doc"][r.doc.request.request_id]:
                if not o.outcome.startswith("detected"):
                    print(f"  MISSED {o.entity_type:<16} [{o.start}:{o.end}] {o.outcome}  {o.best_rejected or ''}")


# ---------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.analyze", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", help="a .txt / .json (DetectionRequest) / .jsonl file (repeatable)")
    ap.add_argument("--input-dir", help="directory of .txt (+ optional .meta.json) / .json / .jsonl files")
    ap.add_argument("--output", help="output directory (default: print to console for <= 3 documents, else results/manual_eval/<time>)")
    ap.add_argument("--redact", action="store_true", help="also redact (engine.redact(RedactionRequest))")
    ap.add_argument("--operator", choices=[o.value for o in Operator], default=None, help="redaction operator (default: policy)")
    ap.add_argument("--gold", help="optional gold JSONL (manual format) for metrics and failure attribution")
    ap.add_argument("--gold-edge-policy", choices=["trim_edge_function_words", "exact"], default="trim_edge_function_words")
    ap.add_argument("--registry", help="registry store directory (subjects/<id>.json, <directory>/<version>.json)")
    ap.add_argument("--subject", help="default subject_context_id for .txt inputs without a .meta.json")
    ap.add_argument("--language", default="en")
    ap.add_argument("--modality", default="imported_text",
                    choices=["typed", "note", "dictation", "telephone", "face_to_face", "asr", "imported_text"])
    ap.add_argument("--normalization-profile", default="asr")
    ap.add_argument("--policy-profile", default="cadence_default")
    ap.add_argument("--config", default=None, help="composition root (default configs/phi/phi.yaml)")
    ap.add_argument("--env", default=None, help="environment: base | dev | test | prod (default PHI_ENV or base)")
    ap.add_argument("--thresholds", default=None, help="extra threshold overlay YAML (evaluation only)")
    ap.add_argument("--no-calibrated", action="store_true",
                    help="do not apply the generated dev-calibrated overlay (configs/phi/generated/)")
    ap.add_argument("--preset", default=None)
    ap.add_argument("--model", "--models", dest="models", default=None, help="comma list or 'none'")
    ap.add_argument("--entities", default=None, help="only these entities / aliases (e.g. PERSON,DOB,PHONE)")
    ap.add_argument("--disable-entities", default="", help="comma list of entities / aliases to disable")
    ap.add_argument("--enable", default="", help="comma list of recognizers / models / normalizers / entities to enable")
    ap.add_argument("--disable", default="", help="comma list of recognizers / models / normalizers / entities to disable")
    ap.add_argument("--set", action="append", default=[], help="dotted.key=value config override")
    ap.add_argument("--mask-text", action="store_true", help="do not write span/document text into outputs")
    ap.add_argument("--device", default="auto")
    return ap


def build_engine(args) -> tuple[PHIEngine, HarnessConfig]:
    from harness.devices import detect_device
    from harness_eval.cli import apply_overlay
    from harness.phi.registry_store import JsonRegistryStore
    cfg = HarnessConfig.load(args.config, env=args.env, apply_generated=False if args.no_calibrated else None)
    if args.preset:
        cfg.apply_preset(args.preset)
    if args.models:
        cfg.only_models([] if args.models.lower() == "none" else args.models.split(","))
    if args.entities:
        cfg.only_entities(args.entities.split(","))
    if args.disable_entities:
        cfg.set_entities(args.disable_entities.split(","), False)
    for flag, on in ((args.enable, True), (args.disable, False)):
        for n in [x.strip() for x in flag.split(",") if x.strip()]:
            if n in cfg.raw["recognizers"]:
                cfg.set_recognizers([n], on)
            elif n in cfg.raw["models"]:
                cfg.raw["models"][n]["enabled"] = on
            elif n in cfg.raw["normalizers"]:
                cfg.raw["normalizers"][n]["enabled"] = on
            else:
                cfg.set_entities([n], on)
    if args.thresholds:
        apply_overlay(cfg, args.thresholds)
    for kv in args.set:
        k, _, v = kv.partition("=")
        cfg.set_value(k, v)
    cfg.validate()
    store = JsonRegistryStore(args.registry or cfg.raw.get("registry", {}).get("directories_path", "data/registry"))
    return PHIEngine(cfg, device=detect_device(args.device), registry_store=store), cfg


def main(argv=None) -> None:
    from harness.cli.common import setup_logging
    args = build_parser().parse_args(argv)
    setup_logging()
    logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)
    docs = collect_inputs(args)
    engine, cfg = build_engine(args)
    results = run(engine, docs, args.redact, args.operator)
    show = not args.mask_text
    gold = None
    if args.gold:
        golds = read_gold(Path(args.gold), docs, args.gold_edge_policy)
        gold = score_gold(engine, results, golds, cfg) if golds else None
    if args.output or len(results) > 3:
        out = Path(args.output) if args.output else ROOT / "results" / "manual_eval" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
        write_outputs(out, results, cfg, show, gold)
        print(f"{len(results)} documents analysed; outputs in {out} (report.md / report.html). Outputs contain text - keep local.")
        if gold:
            s = gold["summary"]
            print(f"gold: mean residual {s['RESIDUAL_PHI_AFTER_SHIELD']['mean']*100:.1f}%  entity full recall "
                  f"{(s['entity_full_recall'] or 0)*100:.1f}%  span precision {(s['span_precision'] or 0)*100:.1f}%  "
                  f"spurious {s['spurious_spans']}  attribution {gold['attribution']}")
    else:
        print_console(results, show, gold)


if __name__ == "__main__":
    sys.exit(main())
