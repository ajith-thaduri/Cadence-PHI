"""Strict typed evaluation: an entity counts as CORRECT only if it is completely hidden AND carries an accepted type.

  python -m harness_eval.strict_eval --input data/diagnostic/user_cases_v1.jsonl [--whole-document] [--model none]
         [--env dev] [--output results/strict/<name>]

Input JSONL, one document per line:
  {"id": ..., "text": ..., "entities": [{"start", "end", "label", "accept": [types...]}],
   "ignore": [{"start", "end", "reason"}]}          # AMBIGUOUS spans: scored neither way

Per gold entity (alphanumeric characters only, the same "core character" idea as the main scorer):
  C  correct      every character covered by REDACT/REVIEW detections, and EVERY covering detection's type is accepted
  W  wrong type   every character covered, type not accepted (protected, mislabelled)
  P  partial      some characters still readable
  M  missing      no character covered
FP: detections that hide alphanumeric characters outside every gold and ignore span (spurious detections and
over-extended spans, e.g. a field label absorbed into a value).
precision = C / (C + W + P + FP)   recall = C / gold   F1   protection = (C + W) / gold
coverage  = hidden gold characters / gold characters   accuracy = C / (gold + FP)

--whole-document joins all documents into one request (how a user pasting them together runs them);
the default is one request per document.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DecisionValue, DetectionOptions, DetectionRequest, Mode

SEP = "\n\n"
TITLE_BEFORE = re.compile(r"\b(?:Dr|Dra|Mr|Mrs|Ms|Mx|Prof|Doctor)\.?\s+$")
POSSESSIVE = re.compile(r"['’]s")


def _alnum(text: str, s: int, e: int) -> set[int]:
    return {i for i in range(s, e) if text[i].isalnum()}


def score_document(text: str, entities: list[dict], ignore: list[dict], detections: list[Any]) -> dict[str, Any]:
    dets = [d for d in detections if str(getattr(d.decision, "value", d.decision)) != "KEEP"]
    owner: dict[int, Any] = {}
    for d in dets:
        for i in range(d.original_start, d.original_end):
            owner[i] = d
    rows = []
    for g in entities:
        core = _alnum(text, g["start"], g["end"])
        hit = [i for i in core if i in owner]
        if not hit:
            outcome, got = "M", None
        else:
            types = Counter(owner[i].entity_type for i in hit)
            bad = [t for t in types if t not in g["accept"]]
            # strict: EVERY hidden character must carry an accepted type ("Daniel R." PROVIDER + "Mercer" CITY is wrong)
            got = bad[0] if bad else types.most_common(1)[0][0]
            if len(types) > 1 and bad:
                got = "/".join(sorted(types))
            if len(hit) < len(core):
                outcome = "P"
            else:
                outcome = "W" if bad else "C"
        exposed = "".join(text[i] for i in sorted(core - set(hit)))
        rows.append({**g, "outcome": outcome, "got": got, "exposed": exposed,
                     "pieces": len({id(owner[i]) for i in hit}), "chars": len(core), "hidden": len(hit)})
    gold_or_ignored = set()
    for s in entities + ignore:
        gold_or_ignored |= set(range(s["start"], s["end"]))
    # annotation guideline: honorifics before a name and a possessive "'s" after an entity belong to the mention
    # but are not core characters - hiding them is neither credit nor over-redaction
    for g in entities:
        m = TITLE_BEFORE.search(text[max(0, g["start"] - 10):g["start"]])
        if m:
            gold_or_ignored |= set(range(g["start"] - (len(m.group(0))), g["start"]))
        if POSSESSIVE.match(text[g["end"]:g["end"] + 2]):
            gold_or_ignored |= {g["end"], g["end"] + 1}
    fps = []
    for d in dets:
        extra = [i for i in _alnum(text, d.original_start, d.original_end) if i not in gold_or_ignored]
        if extra:
            fps.append({"type": d.entity_type, "start": d.original_start, "end": d.original_end,
                        "text": text[d.original_start:d.original_end], "outside": "".join(text[i] for i in extra)})
    return {"entities": rows, "false_positives": fps}


def metrics(rows: list[dict], n_fp: int) -> dict[str, Any]:
    c = Counter(r["outcome"] for r in rows)
    gold = len(rows)
    pred = c["C"] + c["W"] + c["P"] + n_fp
    p = c["C"] / pred if pred else 0.0
    r = c["C"] / gold if gold else 0.0
    chars = sum(x["chars"] for x in rows)
    return {"gold": gold, "correct": c["C"], "wrong_type": c["W"], "partial": c["P"], "missing": c["M"], "false_positives": n_fp,
            "precision": p, "recall": r, "f1": (2 * p * r / (p + r)) if p + r else 0.0,
            "protection": (c["C"] + c["W"]) / gold if gold else 0.0,
            "coverage": sum(x["hidden"] for x in rows) / chars if chars else 0.0,
            "accuracy": c["C"] / (gold + n_fp) if gold + n_fp else 0.0}


def run(docs: list[dict], cfg: HarnessConfig, device: str, whole: bool) -> list[dict]:
    from harness.phi.engine import PHIEngine
    eng = PHIEngine(cfg, device=device, self_test=False)

    def detect(rid: str, text: str):
        req = DetectionRequest(request_id=rid, text=text, modality="note", options=DetectionOptions(mode=Mode.DETECT))
        resp = eng.analyze(req)
        if resp.status != "ok":
            raise RuntimeError(f"{rid}: engine {resp.status} ({resp.failure.code if resp.failure else '?'})")
        return resp.detections

    out = []
    if whole:
        joined, offs, pos = [], [], 0
        for d in docs:
            offs.append(pos)
            joined.append(d["text"])
            pos += len(d["text"]) + len(SEP)
        full = SEP.join(joined)
        allds = detect("strict_whole", full)
        for d, o in zip(docs, offs):
            n = len(d["text"])
            local = [x.model_copy(update={"original_start": max(x.original_start, o) - o, "original_end": min(x.original_end, o + n) - o})
                     for x in allds if x.original_start < o + n and o < x.original_end]
            out.append({"id": d["id"], **score_document(d["text"], d["entities"], d.get("ignore", []), local)})
    else:
        for d in docs:
            out.append({"id": d["id"], **score_document(d["text"], d["entities"], d.get("ignore", []), detect(d["id"], d["text"]))})
    return out


def report(results: list[dict], title: str) -> tuple[str, dict]:
    lines = [f"# Strict typed evaluation - {title}", "",
             "| doc | gold | correct | wrong type | partial | missing | FP | precision | recall | F1 | protected | coverage | accuracy |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    allrows, allfp = [], 0
    for r in results:
        m = metrics(r["entities"], len(r["false_positives"]))
        allrows += r["entities"]
        allfp += len(r["false_positives"])
        lines.append(f"| {r['id']} | {m['gold']} | {m['correct']} | {m['wrong_type']} | {m['partial']} | {m['missing']} | "
                     f"{m['false_positives']} | {m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.1%} | {m['protection']:.1%} | "
                     f"{m['coverage']:.1%} | {m['accuracy']:.1%} |")
    tot = metrics(allrows, allfp)
    lines.append(f"| **total** | {tot['gold']} | {tot['correct']} | {tot['wrong_type']} | {tot['partial']} | {tot['missing']} | "
                 f"{tot['false_positives']} | {tot['precision']:.1%} | {tot['recall']:.1%} | {tot['f1']:.1%} | {tot['protection']:.1%} | "
                 f"{tot['coverage']:.1%} | {tot['accuracy']:.1%} |")
    wrong = Counter(f"{r['label']} -> {r['got']}" for r in allrows if r["outcome"] == "W")
    leaks = Counter(f"{r['label']} ({r['outcome']})" for r in allrows if r["outcome"] in ("P", "M"))
    lines += ["", "## Wrong type (expected -> got)", ""] + [f"- {k}: {v}" for k, v in wrong.most_common()]
    lines += ["", "## Partial / missing", ""] + [f"- {k}: {v}" for k, v in leaks.most_common()]
    lines += ["", "## Details", ""]
    for r in results:
        bad = [e for e in r["entities"] if e["outcome"] != "C"]
        if not bad and not r["false_positives"]:
            continue
        lines.append(f"**{r['id']}**")
        for e in bad:
            extra = f"; exposed `{e['exposed']}`" if e["exposed"] else ""
            lines.append(f"- {e['outcome']} `{e['text']}` expected {e['label']}, got {e['got']}{extra}")
        for f in r["false_positives"]:
            lines.append(f"- FP `{f['text']}` as {f['type']} (outside gold: `{f['outside']}`)")
        lines.append("")
    return "\n".join(lines) + "\n", {"total": tot, "wrong_type": dict(wrong), "leaks": dict(leaks)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.strict_eval")
    ap.add_argument("--input", required=True)
    ap.add_argument("--env", default=None)
    ap.add_argument("--model", default=None, help="comma list or 'none'")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--whole-document", action="store_true")
    ap.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    cfg = HarnessConfig.load(None, env=a.env)
    if a.model:
        cfg.only_models([] if a.model == "none" else a.model.split(","))
    docs = [json.loads(x) for x in Path(a.input).read_text().splitlines() if x.strip()]
    results = run(docs, cfg, a.device, a.whole_document)
    md, summ = report(results, f"{Path(a.input).name} ({'whole document' if a.whole_document else 'per document'}, config {cfg.sha256()[:12]})")
    out = Path(a.output) if a.output else ROOT / "results" / "strict" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md)
    (out / "summary.json").write_text(json.dumps({"config_sha256": cfg.sha256(), "input": a.input, **summ}, indent=1))
    (out / "details.json").write_text(json.dumps(results, indent=1))
    t = summ["total"]
    print(f"gold {t['gold']}  correct {t['correct']}  wrong-type {t['wrong_type']}  partial {t['partial']}  missing {t['missing']}  "
          f"FP {t['false_positives']}  P {t['precision']:.1%}  R {t['recall']:.1%}  F1 {t['f1']:.1%}  protected {t['protection']:.1%}  "
          f"coverage {t['coverage']:.1%} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
