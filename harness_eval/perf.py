"""Performance and reliability harness (T7) - gate G8.

  python -m harness_eval.perf latency     [--n 40] [--model none] [--device cpu]
  python -m harness_eval.perf throughput  [--concurrency 1,2,4] [--n 60]
  python -m harness_eval.perf soak        [--duration 3600]
  python -m harness_eval.perf coldstart   [--repeats 3]
  python -m harness_eval.perf determinism [--devices cpu,mps]
  python -m harness_eval.perf all         [--duration 60]          # every check, one report

The workload is SYNTHETIC only: documents assembled (seeded) from the fabricated CheckList values and
neutral clinical sentences, bucketed by length. No evaluation dataset is read, no text is written to the
outputs (only lengths, timings and counts), and every request goes through the public engine.redact path.
Results: results/perf/<timestamp>/perf.json + perf.md; G8 is evaluated against configs/eval/gates.yaml.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil

from harness.config import load_yaml
from harness.paths import ROOT
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode, stable_id
from harness.phi.engine import PHIEngine
from harness_eval import checklist

BUCKETS = {"<=500": 500, "<=1500": 1500, "<=4000": 4000, "<=8000": 8000}
STAGES = ("normalization_ms", "recognizer_ms", "model_ms", "reconciliation_ms", "policy_ms", "redaction_ms", "total_ms")
GATES = ROOT / "configs" / "eval" / "gates.yaml"


# ---------------------------------------------------------------------------- synthetic workload
def _sentences() -> tuple[list[str], list[str]]:
    t = load_yaml(str(checklist.TEMPLATES))
    phi = [tpl.replace("{v}", v) for spec in (t.get("mft") or {}).values() for tpl in spec["templates"] for v in spec["values"]]
    neutral = list((t.get("dir") or {}).get("must_not_redact", [])) + [checklist.FILLER.strip()]
    return phi, neutral


def workload(bucket: str, n: int, seed: int = 13, phi_ratio: float = 0.3) -> list[str]:
    """n documents just under the bucket's length, about phi_ratio of sentences carrying PHI."""
    phi, neutral = _sentences()
    rng = random.Random(f"{seed}:{bucket}")
    target = BUCKETS[bucket]
    lo = {"<=500": 300, "<=1500": 1000, "<=4000": 3000, "<=8000": 6500}[bucket]
    docs = []
    for _ in range(n):
        parts: list[str] = []
        while sum(len(p) + 1 for p in parts) < lo:
            parts.append(rng.choice(phi if rng.random() < phi_ratio else neutral))
        doc = " ".join(parts)
        while len(doc) > target and len(parts) > 1:
            parts.pop()
            doc = " ".join(parts)
        docs.append(doc)
    return docs


def _req(text: str, i: int, tag: str) -> DetectionRequest:
    return DetectionRequest(request_id=stable_id("req", "perf", tag, i), text=text, language="en",
                            options=DetectionOptions(mode=Mode.REDACT))


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * q
    f = int(k)
    return round(s[f] + (s[min(f + 1, len(s) - 1)] - s[f]) * (k - f), 3)


def _summ(xs: list[float]) -> dict[str, Any]:
    return {"n": len(xs), "p50": _pct(xs, .5), "p95": _pct(xs, .95), "p99": _pct(xs, .99),
            "mean": round(statistics.fmean(xs), 3) if xs else None, "max": round(max(xs), 3) if xs else None}


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / 2**20


def build_engine(cfg: HarnessConfig, device: str) -> PHIEngine:
    return PHIEngine(cfg, device=device, self_test=True)


# ---------------------------------------------------------------------------- checks
def latency(eng: PHIEngine, n: int, warmup: int = 3) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for b in BUCKETS:
        docs = workload(b, n + warmup)
        for i, d in enumerate(docs[:warmup]):
            eng.redact(_req(d, i, f"warm{b}"))
        stage: dict[str, list[float]] = {s: [] for s in STAGES}
        wall, failures = [], 0
        for i, d in enumerate(docs[warmup:]):
            t0 = time.perf_counter()
            resp = eng.redact(_req(d, i, b))
            wall.append((time.perf_counter() - t0) * 1000)
            if resp.status != "ok":
                failures += 1
            for s in STAGES:
                stage[s].append(getattr(resp.timings, s))
        out[b] = {"chars_mean": round(statistics.fmean(len(d) for d in docs[warmup:])), "failures": failures,
                  "wall_ms": _summ(wall), "stages": {s: _summ(v) for s, v in stage.items()}}
    return out


def throughput(eng: PHIEngine, levels: list[int], n: int, bucket: str = "<=4000") -> dict[str, Any]:
    out = {}
    for c in levels:
        docs = workload(bucket, n, seed=29 + c)          # fresh documents per level: no cross-level reuse
        lat: list[float] = []

        def one(i: int) -> str:
            t0 = time.perf_counter()
            st = eng.redact(_req(docs[i], i, f"tp{c}")).status
            lat.append((time.perf_counter() - t0) * 1000)
            return st
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=c) as ex:
            statuses = list(ex.map(one, range(len(docs))))
        sec = time.perf_counter() - t0
        out[str(c)] = {"requests": len(docs), "seconds": round(sec, 3), "req_per_s": round(len(docs) / sec, 2),
                       "non_ok": sum(s != "ok" for s in statuses), "latency_ms": _summ(lat)}
    return out


def soak(eng: PHIEngine, duration_s: float, bucket: str = "<=4000", sample_every_s: float = 5.0) -> dict[str, Any]:
    docs = workload(bucket, 200, seed=41)
    t_start = time.perf_counter()
    samples: list[tuple[float, float]] = []      # (elapsed s, rss MB)
    lat: list[tuple[float, float]] = []
    i, non_ok, next_sample = 0, 0, 0.0
    while (el := time.perf_counter() - t_start) < duration_s:
        if el >= next_sample:
            samples.append((el, _rss_mb()))
            next_sample += sample_every_s
        t0 = time.perf_counter()
        non_ok += eng.redact(_req(docs[i % len(docs)], i, "soak")).status != "ok"
        lat.append((el, (time.perf_counter() - t0) * 1000))
        i += 1
    samples.append((time.perf_counter() - t_start, _rss_mb()))
    # RSS slope after the first 20% (allocator / cache warm-up), least squares, MB per hour
    steady = [s for s in samples if s[0] >= 0.2 * duration_s] or samples
    slope = 0.0
    if len(steady) >= 3:
        mx, my = statistics.fmean(x for x, _ in steady), statistics.fmean(y for _, y in steady)
        den = sum((x - mx) ** 2 for x, _ in steady)
        slope = (sum((x - mx) * (y - my) for x, y in steady) / den) * 3600 if den else 0.0
    k = max(1, len(lat) // 5)
    return {"duration_s": round(samples[-1][0], 1), "requests": i, "non_ok": non_ok,
            "rss_mb_start": round(samples[0][1], 1), "rss_mb_end": round(samples[-1][1], 1),
            "rss_growth_mb_per_hour": round(slope, 2),
            "p95_first_fifth_ms": _pct([x for _, x in lat[:k]], .95), "p95_last_fifth_ms": _pct([x for _, x in lat[-k:]], .95)}


_COLD = r"""
import json, sys, time
t0 = time.perf_counter()
from harness.phi.config import HarnessConfig
from harness.phi.engine import PHIEngine
from harness.phi.contracts import DetectionOptions, DetectionRequest, Mode
t_import = time.perf_counter()
cfg = HarnessConfig.load(sys.argv[1] or None, env=sys.argv[2] or None)
if sys.argv[3] == "none":
    cfg.only_models([])
eng = PHIEngine(cfg, device=sys.argv[4], self_test=True)
t_ready = time.perf_counter()
eng.redact(DetectionRequest(request_id="req_cold", text="Patient called from 415-555-0100 about her refill.",
                            options=DetectionOptions(mode=Mode.REDACT)))
t_first = time.perf_counter()
print(json.dumps({"import_ms": (t_import - t0) * 1000, "ready_ms": (t_ready - t0) * 1000, "first_response_ms": (t_first - t0) * 1000}))
"""


def coldstart(repeats: int, config: Optional[str], env: Optional[str], model: Optional[str], device: str) -> dict[str, Any]:
    runs = []
    for _ in range(repeats):
        p = subprocess.run([sys.executable, "-c", _COLD, config or "", env or "", model or "", device],
                           capture_output=True, text=True, cwd=ROOT, timeout=600,
                           env={**__import__("os").environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        if p.returncode != 0:
            return {"error": f"cold start subprocess exited {p.returncode}"}       # stderr not echoed (may hold paths/text)
        runs.append(json.loads(p.stdout.strip().splitlines()[-1]))
    return {k: _summ([r[k] for r in runs]) for k in runs[0]}


def _signature(eng: PHIEngine, docs: list[str], tag: str) -> list[tuple]:
    sig = []
    for i, d in enumerate(docs):
        r = eng.redact(_req(d, i, tag))
        sig.append(tuple(sorted((x.original_start, x.original_end, x.entity_type, str(x.decision), round(x.decision_score, 4))
                                for x in r.detections)))
    return sig


def determinism(cfg: HarnessConfig, devices: list[str], n: int = 20) -> dict[str, Any]:
    docs = [d for b in BUCKETS for d in workload(b, max(1, n // len(BUCKETS)), seed=53)]
    sigs: dict[str, list] = {}
    out: dict[str, Any] = {"documents": len(docs)}
    for dev in devices:
        eng = build_engine(cfg, dev)
        a, b = _signature(eng, docs, "det_a"), _signature(eng, docs, "det_b")
        out[f"{dev}_repeat_mismatched_docs"] = sum(x != y for x, y in zip(a, b))
        sigs[dev] = a
    ref = devices[0]
    for dev in devices[1:]:
        spans = lambda s: [tuple(t[:4] for t in doc) for doc in s]
        out[f"{ref}_vs_{dev}_span_mismatched_docs"] = sum(x != y for x, y in zip(spans(sigs[ref]), spans(sigs[dev])))
        out[f"{ref}_vs_{dev}_score_mismatched_docs"] = sum(x != y for x, y in zip(sigs[ref], sigs[dev]))
    return out


# ---------------------------------------------------------------------------- gate G8
def evaluate_g8(res: dict[str, Any], device: str) -> dict[str, Any]:
    g = load_yaml(str(GATES))["gates"]["G8"]
    reasons, verdict = [], "PASS"
    lat = (res.get("latency") or {}).get(g["typical_bucket"])
    if not lat:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reasons": ["no latency run"]}
    p95, p99 = lat["wall_ms"]["p95"], lat["wall_ms"]["p99"]
    if p95 > g["max_p95_ms"][device]:
        verdict = "FAIL"
        reasons.append(f"p95 {p95} ms > {g['max_p95_ms'][device]}")
    if p99 > g["max_p99_ms"][device]:
        verdict = "FAIL"
        reasons.append(f"p99 {p99} ms > {g['max_p99_ms'][device]}")
    if lat["failures"]:
        verdict = "FAIL"
        reasons.append(f"{lat['failures']} non-ok responses")
    sk = res.get("soak")
    if not sk:
        verdict = "INSUFFICIENT_EVIDENCE" if verdict == "PASS" else verdict
        reasons.append("no soak run")
    else:
        if sk["rss_growth_mb_per_hour"] > g["max_rss_growth_mb_per_hour"]:
            verdict = "FAIL"
            reasons.append(f"RSS growth {sk['rss_growth_mb_per_hour']} MB/h > {g['max_rss_growth_mb_per_hour']}")
        if sk["duration_s"] < g["min_soak_seconds"] and verdict == "PASS":
            verdict = "INSUFFICIENT_EVIDENCE"
            reasons.append(f"soak {sk['duration_s']} s < required {g['min_soak_seconds']} s")
    det = res.get("determinism")
    if det and any(v for k, v in det.items() if k.endswith("repeat_mismatched_docs")):
        verdict = "FAIL"
        reasons.append("non-deterministic output across repeated runs")
    return {"verdict": verdict, "reasons": reasons, "thresholds": g, "device": device}


def write_report(out: Path, res: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "perf.json").write_text(json.dumps(res, indent=1, default=str))
    L = [f"# Performance (T7) - {res['created']}", "", f"device `{res['device']}`, models `{res['models']}`, "
         f"config `{res['config_sha256']}`, workload: synthetic (no evaluation data)", ""]
    if "latency" in res:
        L += ["## Latency (ms, wall clock per request)", "", "| bucket | chars | p50 | p95 | p99 | model p95 | recognizer p95 | failures |",
              "|---|---|---|---|---|---|---|---|"]
        for b, v in res["latency"].items():
            L.append(f"| {b} | {v['chars_mean']} | {v['wall_ms']['p50']} | {v['wall_ms']['p95']} | {v['wall_ms']['p99']} | "
                     f"{v['stages']['model_ms']['p95']} | {v['stages']['recognizer_ms']['p95']} | {v['failures']} |")
        L.append("")
    if "throughput" in res:
        L += ["## Throughput (<=4000 bucket)", "", "| concurrency | req/s | p95 ms | non-ok |", "|---|---|---|---|"]
        L += [f"| {c} | {v['req_per_s']} | {v['latency_ms']['p95']} | {v['non_ok']} |" for c, v in res["throughput"].items()]
        L.append("")
    for k in ("soak", "coldstart", "determinism"):
        if k in res:
            L += [f"## {k}", "", "```", json.dumps(res[k], indent=1), "```", ""]
    if "G8" in res:
        L += [f"## Gate G8: **{res['G8']['verdict']}**", ""] + [f"- {r}" for r in res["G8"]["reasons"]]
    (out / "perf.md").write_text("\n".join(L) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness_eval.perf")
    ap.add_argument("check", choices=["latency", "throughput", "soak", "coldstart", "determinism", "all"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--env", default=None)
    ap.add_argument("--model", default=None, help="comma list or 'none' (fast tier)")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--devices", default=None, help="determinism: comma list (default: --device plus mps/cuda if present)")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--concurrency", default="1,2,4")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--output", default=None)
    a = ap.parse_args(argv)
    cfg = HarnessConfig.load(a.config, env=a.env)
    if a.model:
        cfg.only_models([] if a.model == "none" else a.model.split(","))
    res: dict[str, Any] = {"created": datetime.now().isoformat(timespec="seconds"), "device": a.device,
                           "models": sorted(cfg.enabled_models) if hasattr(cfg, "enabled_models") else a.model,
                           "config_sha256": cfg.sha256(), "workload": "synthetic:checklist_v1"}
    checks = ["latency", "throughput", "soak", "coldstart", "determinism"] if a.check == "all" else [a.check]
    eng = build_engine(cfg, a.device) if set(checks) & {"latency", "throughput", "soak"} else None
    if "latency" in checks:
        res["latency"] = latency(eng, a.n)
    if "throughput" in checks:
        res["throughput"] = throughput(eng, [int(x) for x in a.concurrency.split(",")], a.n)
    if "soak" in checks:
        res["soak"] = soak(eng, a.duration)
    if "coldstart" in checks:
        res["coldstart"] = coldstart(a.repeats, a.config, a.env, a.model, a.device)
    if "determinism" in checks:
        import torch
        devs = a.devices.split(",") if a.devices else [a.device] + [d for d, ok in (("mps", torch.backends.mps.is_available()),
                                                                               ("cuda", torch.cuda.is_available())) if ok and d != a.device]
        res["determinism"] = determinism(cfg, devs)
    res["G8"] = evaluate_g8(res, a.device)
    out = Path(a.output) if a.output else ROOT / "results" / "perf" / f"{datetime.now():%Y-%m-%d_%H%M%S}"
    write_report(out, res)
    print((out / "perf.md").read_text())
    return 0 if res["G8"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
