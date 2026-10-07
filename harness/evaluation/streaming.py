"""Streaming vs stateless inference for the GLiNER streaming model.

Conversational documents are fed turn by turn (lines; long lines in ~8-word pieces, the
granularity of live ASR output) through a cached incremental session, and compared with
stateless full-text inference on the same documents.
"""
from __future__ import annotations

import re
import time
from typing import Any

from harness.evaluation.matching import match_sample
from harness.metrics.core import gold_block

STREAM_SUBSETS = {"TELEPHONE_CONVERSATION", "NOISY_ASR", "FACE_TO_FACE_DICTATION"}


def stream_chunks(text: str, words_per_chunk: int = 8) -> list[str]:
    pieces = re.findall(r"\S+\s*|\s+", text)
    chunks, cur, n = [], "", 0
    for p in pieces:
        cur += p
        n += 1
        if "\n" in p or n >= words_per_chunk:
            chunks.append(cur)
            cur, n = "", 0
    if cur:
        chunks.append(cur)
    return chunks


def run_streaming_test(adapter, dataset) -> dict[str, Any]:
    if not getattr(adapter, "supports_streaming", False):
        return {"status": "NOT APPLICABLE - model has no streaming session API"}
    thr = adapter.default_threshold
    samples = [s for s in dataset.samples if s.subset in STREAM_SUBSETS]
    gm_stateless, gm_stream, t_chunks = [], [], []
    for i, s in enumerate(samples):
        sl = adapter.decode(adapter.infer_raw(s.text), s.text, thr)
        chunks = stream_chunks(s.text)
        t0 = time.perf_counter()
        st = adapter.infer_stream(chunks, thr, session=f"bench-{i}")
        t_chunks.append((time.perf_counter() - t0) / max(1, len(chunks)))
        gm_stateless += match_sample(s, sl, adapter.mapping.compatible).gold
        gm_stream += match_sample(s, st, adapter.mapping.compatible).gold
    a, b = gold_block(gm_stateless), gold_block(gm_stream)
    return {"documents": len(samples), "subsets": sorted(STREAM_SUBSETS),
            "stateless_recall_full": a["recall_full"], "streaming_recall_full": b["recall_full"],
            "stateless_char_coverage": a["char_coverage"], "streaming_char_coverage": b["char_coverage"],
            "mean_seconds_per_stream_chunk": sum(t_chunks) / max(1, len(t_chunks)), "threshold": thr}
