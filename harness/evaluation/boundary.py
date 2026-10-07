"""Chunk-boundary split test.

For each model, constructs long documents in which a PHI entity deliberately straddles the
model's NAIVE chunk boundary (Stanford/OBI/LabBiomedica: token 510; GLiNER: chunk_words),
then measures full-coverage detection under each long-text strategy:
  naive       hard cut, no overlap -> the entity is split across two windows
  author      the authors' chunking (Stanford break-token windows, GLiNER overlap)
  production  overlapping context windows
Filler is punctuation-free so Stanford's break-token chunker cannot find a sentence break
and must cut at a word boundary (its worst case).
"""
from __future__ import annotations

from typing import Any

from harness.adapters.base import BaseModelAdapter
from harness.adapters.chunking import word_spans
from harness.evaluation.matching import match_sample
from harness.schema import GoldEntity, Sample

FILLER = ("the member was comfortable and resting with family at the bedside and the care team reviewed "
          "the plan of care with her in detail and answered all of her questions about the medications ")
CASES = {
    "name_split": ("spoke with ", "Maria Elena Thompson", "NAME", " about the plan "),
    "address_split": ("she lives at ", "1450 Oak Street Springfield", "ADDRESS", " with her husband "),
    "phone_split": ("she can be reached at ", "(312) 555-0147", "PHONE", " after noon "),
    "mrn_split": ("medical record number ", "4829103", "MRN", " was verified "),
    "date_split": ("she was born on ", "January 2, 1988", "DOB", " in Chicago "),
}


def _units(adapter: BaseModelAdapter, text: str) -> list[tuple[int, int]]:
    if hasattr(adapter, "tokenizer") and adapter.tokenizer is not None:
        enc = adapter.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        return [tuple(x) for x in enc["offset_mapping"]]
    return word_spans(text)


def _naive_boundary(adapter: BaseModelAdapter) -> int:
    if hasattr(adapter, "icfg"):
        return int(adapter.icfg.get("max_tokens", 512)) - 2
    return int(adapter.gcfg.get("aggregation", {}).get("chunk_words", 2000))


def build_cases(adapter: BaseModelAdapter) -> list[Sample]:
    boundary = _naive_boundary(adapter)
    samples = []
    for case, (pre, ent, label, post) in CASES.items():
        for shift in range(3):
            # grow filler until the boundary unit falls inside the entity (shifted 0..2 units)
            n_words = 1
            text = start = None
            filler_words = FILLER.split()
            lo, hi = 1, boundary * 3
            while lo <= hi:
                mid = (lo + hi) // 2
                prefix = " ".join(filler_words[i % len(filler_words)] for i in range(mid)) + " " + pre
                units = _units(adapter, prefix + ent)
                idx = next((k for k, (s, e) in enumerate(units) if e > len(prefix)), len(units))
                if idx + shift + 1 <= boundary:
                    n_words, lo = mid, mid + 1
                else:
                    hi = mid - 1
            prefix = " ".join(filler_words[i % len(filler_words)] for i in range(n_words)) + " " + pre
            start = len(prefix)
            suffix = post + " ".join(filler_words[i % len(filler_words)] for i in range(200)) + "."
            text = prefix + ent + suffix
            units = _units(adapter, text)
            ent_units = [k for k, (s, e) in enumerate(units) if s < start + len(ent) and e > start]
            straddles = bool(ent_units) and ent_units[0] < boundary <= ent_units[-1]
            samples.append(Sample(id=f"{case}_{shift}", text=text,
                                  entities=[GoldEntity(start=start, end=start + len(ent), text=ent, label=label)],
                                  metadata={"subset": "CHUNK_BOUNDARY", "language": "en", "source": "synthetic",
                                            "case": case, "straddles_naive_boundary": straddles}))
    return samples


def run_boundary_test(adapter: BaseModelAdapter) -> dict[str, Any]:
    samples = build_cases(adapter)
    original = adapter.long_text_strategy
    thr = adapter.default_threshold
    rows = []
    try:
        for strat in ("naive", "author", "production"):
            adapter.long_text_strategy = strat
            for s in samples:
                raw = adapter.infer_raw(s.text, None if adapter.filterable else thr)
                preds = adapter.decode(raw, s.text, thr)
                g = match_sample(s, preds, adapter.mapping.compatible).gold[0]
                bounds = raw.truncation.chunk_char_bounds
                split = any(g.entity.start < b < g.entity.end for b in bounds)
                rows.append({"strategy": strat, "case": s.metadata["case"], "sample": s.id,
                             "entity_split_by_chunker": split, "full_coverage": g.full,
                             "coverage": round(g.coverage, 3), "num_chunks": raw.truncation.num_chunks,
                             "straddles_naive_boundary": s.metadata["straddles_naive_boundary"]})
    finally:
        adapter.long_text_strategy = original
    summary: dict[str, Any] = {}
    for strat in ("naive", "author", "production"):
        r = [x for x in rows if x["strategy"] == strat]
        summary[strat] = {
            "entities_split": sum(x["entity_split_by_chunker"] for x in r),
            "full_coverage_rate": sum(x["full_coverage"] for x in r) / len(r),
            "by_case": {c: sum(x["full_coverage"] for x in r if x["case"] == c) / max(1, sum(1 for x in r if x["case"] == c))
                        for c in CASES},
        }
    return {"cases": len(samples), "naive_boundary_units": _naive_boundary(adapter), "summary": summary, "rows": rows}
