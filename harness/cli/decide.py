"""Contextual-ML-layer decision experiment.

  python -m harness.decide build      # authored JSONL -> cadence_text.jsonl + cadence_asr.jsonl (TTS->phone->Whisper)
  python -m harness.decide predict    # one subprocess per candidate, frozen thresholds
  python -m harness.decide evaluate   # singles, pairs (+ triple/cascade only if protocol allows)
  python -m harness.decide report     # reports/decision_report.{md,html}
  python -m harness.decide all
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from harness.cli.common import setup_logging
from harness.paths import RESULTS, ROOT

log = logging.getLogger("harness.decide")
AUTHORED = ROOT / "data" / "cadence_realistic" / "authored"
OUTD = ROOT / "data" / "cadence_realistic"


def _out_dir(create: bool) -> Path:
    latest = RESULTS / "DECISION_LATEST"
    if not create and latest.exists():
        return RESULTS / latest.read_text().strip()
    d = RESULTS / f"decision_{datetime.now():%Y-%m-%d_%H%M%S}"
    d.mkdir(parents=True, exist_ok=True)
    latest.write_text(d.name)
    return d


# Same-day references are not identifiers under configs/redaction_policy.yaml (the call date is
# document metadata). Authors sometimes marked them; they become ignore spans for EVERY model.
SAME_DAY = {"today", "tonight", "this morning", "this afternoon", "this evening", "right now", "now",
            "hoy", "esta mañana", "esta tarde", "esta noche", "ahorita"}


def _policy_split(ents: list[dict]) -> tuple[list[dict], list[dict]]:
    keep, ign = [], []
    for e in ents:
        (ign if e["label"] == "DATE" and e["text"].strip().lower() in SAME_DAY else keep).append(e)
    return keep, ign


def build(device: str, authored: Path = AUTHORED, prefix: str = "cadence") -> None:
    from harness.decision.asr import Transcriber, build_asr_document
    from harness.decision.markup import parse
    from harness.schema import GoldEntity, IgnoreSpan, Sample

    recs = []
    for p in sorted(authored.glob("*.jsonl")):
        recs += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    text_rows = []
    for r in recs:
        t, ents = parse(r["text"])
        ents, ign = _policy_split(ents)
        s = Sample(id=r["id"], text=t, entities=[GoldEntity(**e, difficulty="unknown", tags=[]) for e in ents],
                   ignore_spans=[IgnoreSpan(start=e["start"], end=e["end"], reason="same-day reference (policy)") for e in ign],
                   metadata={"subset": f"TIER3_{r['doc_type'].upper()}", "language": r["language"], "doc_type": r["doc_type"],
                             "spoken": r.get("spoken", False), "source": "realistic", "registry": r["registry"],
                             "scenario": r.get("scenario", ""), "evidence_label": "TIER 3 - clean authored text"})
        s.validate_offsets()
        text_rows.append(s.model_dump())
    (OUTD / f"{prefix}_text.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in text_rows) + "\n")
    tr = Transcriber(device)
    asr_rows, dropped, garbled_all = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, r in enumerate([r for r in recs if r.get("spoken")]):
            doc = build_asr_document(r, tr, Path(tmp))
            dropped += [{"doc": r["id"], **d} for d in doc["dropped_by_asr"]]
            kept, ign = _policy_split([{**e, "text": e.get("ref_text", e["text"])} for e in doc["entities"]])
            ign_ids = {(e["start"], e["end"]) for e in ign}
            ents = [GoldEntity(start=e["start"], end=e["end"], text=e["text"], label=e["label"], difficulty="unknown",
                               tags=["asr_altered"] if e["asr_altered"] else []) for e in doc["entities"]
                    if (e["start"], e["end"]) not in ign_ids]
            garb = [IgnoreSpan(start=e["start"], end=e["end"], reason=f"garbled by ASR (similarity {e['similarity']}) - not leaked PHI")
                    for e in doc["garbled_by_asr"]]
            garbled_all += [{"doc": r["id"], "label": e["label"], "ref": e["ref_text"], "asr": e["text"], "similarity": e["similarity"]}
                            for e in doc["garbled_by_asr"]]
            s = Sample(id=doc["id"], text=doc["text"], entities=ents,
                       ignore_spans=[IgnoreSpan(start=a, end=b, reason="same-day reference (policy)") for a, b in ign_ids] + garb,
                       metadata={"repetition_loop": doc["repetition_loop"],
                "subset": f"TIER3_ASR_{r['doc_type'].upper()}", "language": r["language"], "doc_type": r["doc_type"],
                "spoken": True, "source": "realistic", "registry": r["registry"], **doc["metadata"],
                "evidence_label": "TIER 3 - real ASR engine output (TTS speech, telephone channel)"})
            s.validate_offsets()
            asr_rows.append(s.model_dump())
            if (i + 1) % 5 == 0:
                log.info("ASR %d documents", i + 1)
    (OUTD / f"{prefix}_asr.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in asr_rows) + "\n")
    (OUTD / ("asr_dropped_entities.json" if prefix == "cadence" else f"{prefix}_asr_dropped_entities.json")).write_text(json.dumps(dropped, indent=1, ensure_ascii=False))
    (OUTD / ("asr_garbled_entities.json" if prefix == "cadence" else f"{prefix}_asr_garbled_entities.json")).write_text(json.dumps(garbled_all, indent=1, ensure_ascii=False))
    log.info("built %d text docs, %d ASR docs, %d entities dropped by ASR", len(text_rows), len(asr_rows), len(dropped))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.decide", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["build", "build-dev", "predict", "predict-one", "evaluate", "report", "all"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)
    setup_logging()
    from harness.devices import detect_device
    dev = detect_device(args.device)
    if args.step in ("build", "all"):
        build(dev)
    if args.step == "build-dev":   # development split: thresholds, calibration and conversational rules ONLY
        build(dev, AUTHORED.parent / "authored_dev", "cadence_dev")
    if args.step in ("predict", "all"):
        out = _out_dir(create=True)
        from harness.decision.experiment import CANDIDATES
        for m in CANDIDATES:
            subprocess.run([sys.executable, "-m", "harness.decide", "predict-one", "--model", m, "--device", dev], check=True)
        log.info("predictions in %s", out)
    if args.step == "predict-one":
        from harness.decision.experiment import predict_model
        predict_model(args.model, _out_dir(create=False), dev)
    if args.step in ("evaluate", "all"):
        from harness.decision.experiment import run_evaluation
        out = _out_dir(create=False)
        R = run_evaluation(out)
        (out / "decision_results.json").write_text(json.dumps(R, indent=1, default=str))
        log.info("decision results: %s", out / "decision_results.json")
    if args.step in ("report", "all"):
        from harness.decision.report import write_report
        write_report(_out_dir(create=False))


if __name__ == "__main__":
    main()
