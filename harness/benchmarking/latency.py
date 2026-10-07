"""Latency / throughput / memory benchmark.

Download time is never measured: models must already be in the local cache (OFFLINE_MODE).
  load time      in-process from_pretrained + to(device)
  cold start     fresh subprocess: interpreter + imports + load + first prediction
  warm latency   single-document predict(), after >=3 warm-up iterations, >=20 measured
  batch latency  infer_raw_batch over a fixed batch
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import sys
import time
from typing import Any

import numpy as np
import psutil

from harness.adapters import build_adapter
from harness.config import benchmark_config
from harness.paths import ROOT

log = logging.getLogger(__name__)


def _stats(xs: list[float]) -> dict[str, float]:
    a = np.array(xs) * 1000.0
    return {"mean_ms": float(a.mean()), "median_ms": float(np.median(a)),
            "p95_ms": float(np.percentile(a, 95)), "min_ms": float(a.min()), "max_ms": float(a.max()),
            "stdev_ms": float(statistics.pstdev(a)), "n": len(xs)}


def _accel_mem(device: str) -> dict[str, Any]:
    import torch

    if device == "cuda":
        return {"gpu_allocated_mb": torch.cuda.memory_allocated() / 1e6,
                "gpu_peak_allocated_mb": torch.cuda.max_memory_allocated() / 1e6}
    if device == "mps":
        return {"mps_allocated_mb": torch.mps.current_allocated_memory() / 1e6,
                "mps_driver_allocated_mb": torch.mps.driver_allocated_memory() / 1e6}
    return {}


def cold_start(key: str, device: str, text: str) -> dict[str, Any]:
    code = (
        "import time,json,sys;t0=time.perf_counter();"
        "import harness;from harness.cli.common import setup_logging;setup_logging();"
        "from harness.adapters import build_adapter;"
        f"a=build_adapter({key!r},{device!r});ok=a.load();t1=time.perf_counter();"
        "p=a.predict(sys.stdin.read()) if ok else [];t2=time.perf_counter();"
        "print(json.dumps({'ok':ok,'import_and_load_s':t1-t0,'first_predict_s':t2-t1,'cold_start_total_s':t2-t0}))"
    )
    env = {**os.environ, "OFFLINE_MODE": "true"}
    t = time.perf_counter()
    r = subprocess.run([sys.executable, "-c", code], input=text, capture_output=True, text=True,
                       cwd=ROOT, env=env, timeout=900)
    wall = time.perf_counter() - t
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        out = {"ok": False, "error": r.stderr[-800:]}
    out["subprocess_wall_s"] = wall
    return out


def benchmark_model(key: str, device: str, texts: list[str], batch_texts: list[str],
                    warmup: int | None = None, iters: int | None = None, do_cold: bool = True) -> dict[str, Any]:
    cfg = benchmark_config()["benchmark"]
    warmup = max(3, int(warmup or cfg["warmup_iterations"]))
    iters = max(20, int(iters or cfg["measured_iterations"]))
    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    import torch
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()

    adapter = build_adapter(key, device)
    ok = adapter.load()
    res: dict[str, Any] = {"model": key, "device": device, "status": adapter.status.value,
                           "load_time_s": adapter.load_time_s, "model_file_bytes": adapter.model_file_bytes()}
    if not ok:
        res["error"] = adapter.load_error
        return res
    res["rss_after_load_mb"] = proc.memory_info().rss / 1e6
    res["rss_delta_load_mb"] = (proc.memory_info().rss - rss0) / 1e6
    res.update({f"after_load_{k}": v for k, v in _accel_mem(device).items()})
    peak_rss = proc.memory_info().rss

    def sync():
        if device == "cuda":
            torch.cuda.synchronize()
        elif device == "mps":
            torch.mps.synchronize()

    # warm-up
    for i in range(warmup):
        adapter.predict(texts[i % len(texts)])
    sync()
    # single-document latency
    lat = []
    for i in range(iters):
        t = texts[i % len(texts)]
        t0 = time.perf_counter()
        adapter.predict(t)
        sync()
        lat.append(time.perf_counter() - t0)
        peak_rss = max(peak_rss, proc.memory_info().rss)
    res["single_doc"] = _stats(lat)
    words = [len(texts[i % len(texts)].split()) for i in range(iters)]
    res["words_per_second"] = sum(words) / sum(lat)
    res["docs_per_second_single"] = iters / sum(lat)
    tok = getattr(adapter, "tokenizer", None)
    if tok is not None:
        ntok = [len(tok(texts[i % len(texts)], add_special_tokens=False)["input_ids"]) for i in range(iters)]
        res["tokens_per_second"] = sum(ntok) / sum(lat)
    # batch latency
    blat = []
    for _ in range(max(3, iters // 4)):
        t0 = time.perf_counter()
        adapter.infer_raw_batch(batch_texts)
        sync()
        blat.append(time.perf_counter() - t0)
        peak_rss = max(peak_rss, proc.memory_info().rss)
    res["batch"] = {**_stats(blat), "batch_size": len(batch_texts)}
    res["docs_per_second_batch"] = len(batch_texts) / float(np.median(blat))
    res["peak_rss_mb"] = peak_rss / 1e6
    res.update({f"peak_{k}": v for k, v in _accel_mem(device).items()})
    res["mean_words_per_doc"] = float(np.mean([len(t.split()) for t in texts]))
    adapter.unload()
    if do_cold:
        res["cold_start"] = cold_start(key, device, texts[0])
    if device == "cpu" and res["single_doc"]["median_ms"] > 5000:
        res["practicality_note"] = "median single-document latency > 5 s on CPU - impractical for interactive use"
    return res
