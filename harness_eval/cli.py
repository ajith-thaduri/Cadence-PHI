"""Command-line entry points. All of them speak the canonical contracts (harness/phi/contracts.py).

  # batch: one DetectionRequest per line -> predictions.jsonl (one DetectionResponse per line)
  python -m harness.evaluate --config configs/phi/phi.yaml --input data/requests/cadence_text.requests.jsonl \\
         [--gold data/requests/cadence_text.gold.jsonl] [--registry data/requests/registry]
  # benchmark dataset (requests + gold + scoped registry built by harness/phi/datasets.py)
  python -m harness.evaluate --config configs/phi/phi.yaml --dataset cadence_asr [--model obi_roberta] \\
         [--entities PERSON,DOB,PHONE,MRN] [--disable DATE,LOCATION] [--preset OBI_FULL]
  python -m harness.calibrate --dataset cadence_dev          # development data only; refuses test data
  python -m harness.compare --dataset cadence_test            # ablation ladder, second-model test, ASR analysis
  python -m harness.shield_inspect --text "..." [--redact]    # one document, evidence chain printed (local)
  python -m harness_eval.export --dataset cadence_test         # write canonical requests/gold JSONL

Overrides (all configuration, never code): --model/--models, --entities, --enable/--disable
(entities, aliases, recognizers, models, normalizers), --preset, --thresholds overlay.yaml,
--set dotted.key=value, --show-text/--mask-text.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

from harness.cli.common import setup_logging
from harness.config import load_yaml
from harness.paths import ROOT
from harness.phi.config import ConfigError, HarnessConfig

log = logging.getLogger("harness.phi")
logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)


def common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config", default=None, help="composition root (default configs/phi/phi.yaml) or a legacy single file")
    ap.add_argument("--env", default=None, help="environment overlay: base | dev | test | prod (default PHI_ENV or base)")
    ap.add_argument("--no-calibrated", action="store_true", help="do not apply the generated (dev-calibrated) threshold overlay")
    ap.add_argument("--dataset", action="append", default=None, help="dataset or alias (repeatable / comma list)")
    ap.add_argument("--model", "--models", dest="models", default=None, help="comma list; 'none' for no model")
    ap.add_argument("--entities", default=None, help="only these entities/aliases")
    ap.add_argument("--enable", default="", help="comma list of entities/aliases/recognizers/models/normalizers")
    ap.add_argument("--disable", default="", help="comma list of entities/aliases/recognizers/models/normalizers")
    ap.add_argument("--preset", "--profile", dest="preset", default=None, help="named preset from the config")
    ap.add_argument("--thresholds", default=None, help="threshold overlay YAML written by harness.calibrate")
    ap.add_argument("--set", action="append", default=[], help="dotted.key=value override")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--refresh", action="store_true", help="recompute cached model inference")
    ap.add_argument("--out", default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--show-text", action="store_true")
    g.add_argument("--mask-text", action="store_true")


def apply_overlay(cfg: HarnessConfig, path: str) -> None:
    ov = load_yaml(path)
    t = ov.get("thresholds") or {}
    for m, v in (t.get("model") or {}).items():
        cfg.set_value(f"thresholds.model.{m}", v)
    for m, per in (t.get("model_entity") or {}).items():
        for e, v in (per or {}).items():
            cfg.set_value(f"thresholds.model_entity.{m}.{e}", v)
    for e, v in (t.get("entity") or {}).items():
        cfg.set_value(f"thresholds.entity.{e}", v)
    cfg.raw.setdefault("_overlays", []).append(path)


def build_config(args) -> HarnessConfig:
    cfg = HarnessConfig.load(args.config, env=args.env, apply_generated=False if args.no_calibrated else None)
    if args.preset:
        cfg.apply_preset(args.preset)
    if args.models:
        cfg.only_models([] if args.models.lower() == "none" else args.models.split(","))
    if args.entities:
        cfg.only_entities(args.entities.split(","))
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
    if args.show_text:
        cfg.raw.setdefault("output", {})["show_text"] = "always"
    if args.mask_text:
        cfg.raw.setdefault("output", {})["show_text"] = "never"
    cfg.validate()
    return cfg


def _datasets(args, default: list[str]) -> list[str]:
    from harness_eval.datasets import expand
    names = []
    for d in args.dataset or default:
        names += [x.strip() for x in d.split(",") if x.strip()]
    return expand(names)


def _out(args, kind: str) -> Path:
    return Path(args.out) if args.out else ROOT / "results" / "phi" / f"{kind}_{datetime.now():%Y-%m-%d_%H%M%S}"


def _device(args) -> str:
    from harness.devices import detect_device
    return detect_device(args.device)


def _print(results) -> None:
    for r in results:
        s = r["summary"]
        rr = s["RESIDUAL_PHI_AFTER_SHIELD"]
        print(f"{r['dataset']:<20} docs={r['documents']:<3} residual mean={rr['mean']*100:5.1f}%  p95={rr['p95']*100:5.1f}%  "
              f"sanitised={(s['complete_document_sanitization_rate'] or 0)*100:5.1f}%  "
              f"high-risk full recall={(s['high_risk_full_recall'] or 0)*100:5.1f}%  "
              f"precision={(s['span_precision'] or 0)*100:5.1f}%  spurious={s['spurious_spans']}")


# ---------------------------------------------------------------------------- evaluate
def evaluate_main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.evaluate --config ...", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(ap)
    ap.add_argument("--input", default=None, help="JSONL of DetectionRequest (one per line)")
    ap.add_argument("--gold", default=None, help="JSONL of GoldDocument for --input (enables SCORE)")
    ap.add_argument("--registry", default=None, help="registry store directory for --input (subjects/, directories)")
    ap.add_argument("--curves", action="store_true", help="also compute per model x entity threshold curves")
    args = ap.parse_args(argv)
    setup_logging()
    cfg = build_config(args)
    out = _out(args, "eval")
    if args.input:
        return _evaluate_jsonl(args, cfg, out)
    from harness_eval.scoring import prepare, score, threshold_curves, write_outputs
    names = _datasets(args, ["cadence_test"])
    bundles, results, curves = [], [], []
    for n in names:
        b = prepare(n, cfg, _device(args), args.refresh)
        bundles.append(b)
        results.append(score(b, cfg))
        if args.curves:
            curves += threshold_curves(b, cfg)
    write_outputs(out, bundles, results, cfg, curves if args.curves else None,
                  {"command": "evaluate", "datasets": names, "overlays": cfg.raw.get("_overlays", [])})
    _print(results)
    print(f"\noutputs: {out}")


def _evaluate_jsonl(args, cfg: HarnessConfig, out: Path) -> None:
    """Canonical batch mode: requests JSONL in, predictions JSONL out (+ scoring if --gold)."""
    from harness_eval.datasets import read_gold, read_requests
    from harness.phi.engine import PHIEngine
    from harness.phi.registry_store import JsonRegistryStore
    reqs = read_requests(Path(args.input))
    store = JsonRegistryStore(args.registry or cfg.raw.get("registry", {}).get("directories_path", "data/registry"))
    eng = PHIEngine(cfg, device=_device(args), registry_store=store)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "predictions.jsonl", "w", encoding="utf-8") as f:
        for r in reqs:
            f.write(eng.analyze(r).model_dump_json() + "\n")
    print(f"predictions: {out / 'predictions.jsonl'} ({len(reqs)} responses)")
    if args.gold:
        from harness_eval.scoring import Bundle, score, write_outputs
        golds = {g.request_id: g for g in read_gold(Path(args.gold))}
        col = {r.request_id: eng.collect(r) for r in reqs}
        b = Bundle(Path(args.input).name.split(".")[0], "realistic" if not args.mask_text else "real", reqs, golds, col, eng)
        res = score(b, cfg)
        write_outputs(out, [b], [res], cfg, None, {"command": "evaluate --input", "input": args.input, "gold": args.gold})
        _print([res])


# ---------------------------------------------------------------------------- calibrate
def calibrate_main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.calibrate", description="Per model x entity thresholds from DEV data only.")
    common_args(ap)
    ap.add_argument("--tolerance", type=float, default=0.01, help="allowed entity residual above the minimum (fraction of core chars)")
    ap.add_argument("--min-support", type=int, default=8)
    ap.add_argument("--write", default="configs/phi/generated/thresholds_calibrated.yaml")
    args = ap.parse_args(argv)
    setup_logging()
    from harness_eval.calibrate import run_calibration
    from harness_eval import manifests
    cfg = build_config(args)
    names = _datasets(args, ["cadence_dev"])
    try:   # roles come from the signed manifest (configs/eval/datasets.yaml), never from dataset names
        manifests.require_role(names, {"dev"}, "calibration")
    except manifests.ManifestError as exc:
        raise SystemExit(f"refusing to calibrate: {exc} (no test-set threshold tuning)") from None
    run_calibration(cfg, names, _device(args), _out(args, "calibrate"), Path(args.write), args.tolerance,
                    args.min_support, args.refresh)


# ---------------------------------------------------------------------------- compare
def compare_main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.compare", description="Ablation ladder / preset comparison.")
    common_args(ap)
    ap.add_argument("--presets", default=None, help="comma list (default: every preset + normalizer ablations)")
    args = ap.parse_args(argv)
    setup_logging()
    from harness_eval.compare import run_compare
    cfg = build_config(args)
    run_compare(cfg, _datasets(args, ["cadence_test"]), _device(args), _out(args, "compare"),
                args.presets.split(",") if args.presets else None, args.refresh)


# ---------------------------------------------------------------------------- inspect one document
def inspect_main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness.shield_inspect",
                                 description="Run the engine on ONE local text/audio and print the evidence chain (local only).")
    common_args(ap)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text")
    src.add_argument("--file")
    src.add_argument("--request", help="a DetectionRequest JSON file")
    src.add_argument("--audio", help="local WAV -> local Whisper ASR -> engine")
    ap.add_argument("--language", default="en")
    ap.add_argument("--normalization-profile", default="asr")
    ap.add_argument("--registry", default=None, help="registry store directory (with --request)")
    ap.add_argument("--redact", action="store_true", help="REDACT mode: print sanitized text")
    ap.add_argument("--json", action="store_true", help="print the full DetectionResponse JSON")
    args = ap.parse_args(argv)
    setup_logging()
    from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode, stable_id
    from harness.phi.engine import PHIEngine
    from harness.phi.registry_store import JsonRegistryStore
    cfg = build_config(args)
    show = str(cfg.raw.get("output", {}).get("show_text")) in ("always", "true", "True")
    if args.request:
        req = DetectionRequest.model_validate_json(Path(args.request).read_text())
    else:
        if args.audio:
            from harness.phi.asr import WhisperASR
            text, modality = WhisperASR(_device(args)).transcribe(Path(args.audio), args.language), "asr"
        else:
            text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
            modality = "imported_text"
        req = DetectionRequest(request_id=stable_id("req", "inspect", text), text=text, language=args.language,
                               modality=modality, normalization_profile=args.normalization_profile,
                               options=DetectionOptions(mode=Mode.DEBUG, include_text=show))
    eng = PHIEngine(cfg, device=_device(args), registry_store=JsonRegistryStore(args.registry or "data/registry"))
    resp = eng.redact(req) if args.redact else eng.analyze(req)
    if args.json:
        print(resp.model_dump_json(indent=1))
        return
    ev = {e.evidence_id: e for e in resp.evidence}
    for d in resp.detections:
        print(f"{d.decision:<7} {d.entity_type:<16} [{d.original_start}:{d.original_end}] {d.text or '':<34} "
              f"{d.confidence_class:<6} {d.decision_reason}")
        for eid in d.contributing_evidence_ids:
            e = ev.get(eid)
            if e:
                print(f"         + {e.recognizer_id:<22} {e.entity_type:<16} {e.raw_score:<7.4f} {e.score_type:<20} "
                      f"thr {e.threshold_used} ({e.threshold_source}) view={e.text_view_id}")
        for r in d.rejected_evidence:
            print(f"         - {r.recognizer_id:<22} {r.entity_type:<16} {r.raw_score:<7.4f} {r.reason:<20} thr {r.threshold_used}")
    if resp.output.sanitized_text is not None:
        print("\n" + resp.output.sanitized_text)
    print(f"\n{resp.summary.entities_detected} detections, {resp.summary.candidates} candidates, {resp.timings.total_ms:.0f} ms")
