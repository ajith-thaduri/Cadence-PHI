"""Adapter for GLiNER models (span, token-level and streaming variants).

Span models: inference runs once at the sweep floor; flat_ner decoding is greedy by
descending score, so a higher threshold is exactly a filter (filterable=True).
Token-level models (UniEncoderTokenGLiNER): span shapes depend on the threshold, so the
runner re-infers at each threshold it reports (filterable=False).

Long-text strategies (words = GLiNER whitespace-splitter tokens):
  author      `aggregation` in configs/gliner/<model>.yaml (e.g. LOGICAL tool: 2000/25)
  production  `production` block: short overlapping chunks (default 256 words, 64 overlap)
  naive       hard cuts every chunk_words words, no overlap (worst case for boundaries)
  truncate    single pass; GLiNER itself truncates at max_len words (reported)

Weights load from the pinned local snapshot directory (no network, and no need for the
ONNX exports that some repos also ship).
"""
from __future__ import annotations

import logging
import os
import warnings
from typing import Optional

from harness.adapters.base import BaseModelAdapter, RawOutput
from harness.adapters.chunking import word_spans, word_windows
from harness.config import load_yaml
from harness.schema import Prediction, TruncationInfo

log = logging.getLogger(__name__)
RAW_FLOOR = 0.10


def snapshot_dir(model_id: str, revision: str) -> str:
    from huggingface_hub import try_to_load_from_cache

    p = try_to_load_from_cache(model_id, "gliner_config.json", revision=revision)
    if not isinstance(p, str):
        raise FileNotFoundError(f"{model_id}@{revision} not in local cache - run `OFFLINE_MODE=false python -m harness.download`")
    return os.path.dirname(p)


class GLiNERAdapter(BaseModelAdapter):
    supports_scores = True

    def __init__(self, key: str, device: str, long_text_strategy: str = "author") -> None:
        super().__init__(key, device, long_text_strategy)
        self.gcfg = load_yaml(self.entry["gliner_config"])
        self.labels: list[str] = list(self.gcfg["labels"])
        unknown = set(self.labels) - set(self.mapping.native_labels)
        if unknown:
            raise ValueError(f"{key}: requested labels missing from label mapping: {unknown}")
        self.filterable = True

    @property
    def default_threshold(self) -> Optional[float]:
        return float(self.gcfg["threshold"])

    def max_input_length(self) -> tuple[int, str]:
        return int(self.gcfg["max_length"]), "words"

    def get_native_labels(self) -> list[str]:
        return list(self.gcfg.get("native_trained_labels") or [])

    def get_metadata(self):
        md = super().get_metadata()
        md["requested_labels"] = self.labels
        md["gliner_config"] = self.gcfg
        md["label_provenance_by_label"] = self.mapping.label_provenance
        return md

    def _load(self) -> None:
        import torch
        from gliner import GLiNER

        path = snapshot_dir(self.entry["model_id"], self.entry["revision"])
        kw = {"load_tokenizer": True} if self.gcfg.get("load_tokenizer") else {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = GLiNER.from_pretrained(path, **kw)
        if self.gcfg.get("add_prefix_space"):
            try:
                model.data_processor.transformer_tokenizer.add_prefix_space = True
            except AttributeError:
                self.runtime_notes.append("add_prefix_space could not be set")
        if int(model.config.max_len) != int(self.gcfg["max_length"]):
            raise RuntimeError(f"gliner_config max_len={model.config.max_len} != configs max_length={self.gcfg['max_length']}")
        cls = type(model).__name__
        self.filterable = "Token" not in cls
        self.runtime_notes.append(f"GLiNER class {cls}; filterable thresholds={self.filterable}")
        self.model = model.to(torch.device(self.device)).eval()
        self._torch = torch

    WORD_BUDGET = 1600   # max total words per forward batch (keeps accelerator memory bounded on 16 GB machines)

    def _run(self, texts: list[str], threshold: float) -> list[list[dict]]:
        """Inference with an adaptive batch size: long chunks run one or two at a time."""
        if not texts:
            return []
        out: list[list[dict]] = []
        i = 0
        while i < len(texts):
            batch, words = [], 0
            while i < len(texts) and len(batch) < 8:
                w = len(texts[i].split())
                if batch and words + w > self.WORD_BUDGET:
                    break
                batch.append(texts[i])
                words += w
                i += 1
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with self._torch.inference_mode():
                    out.extend(self.model.inference(
                        batch, self.labels, flat_ner=self.gcfg.get("overlap_policy", "flat_ner") == "flat_ner",
                        threshold=threshold, multi_label=bool(self.gcfg.get("multi_label", False)), batch_size=len(batch)))
            if words > 800 and self.device == "mps":
                self._torch.mps.empty_cache()
        return out

    def _chunk_params(self) -> tuple[int, int]:
        if self.long_text_strategy == "production":
            p = self.gcfg.get("production", {})
            return int(p.get("chunk_words", 256)), int(p.get("overlap_words", 64))
        agg = self.gcfg.get("aggregation", {})
        size = int(agg.get("chunk_words", 2000))
        return (size, 0) if self.long_text_strategy == "naive" else (size, int(agg.get("overlap_words", 25)))

    def infer_raw(self, text: str, threshold: Optional[float] = None) -> RawOutput:
        return self.infer_raw_batch([text], threshold)[0]

    def infer_raw_batch(self, texts: list[str], threshold: Optional[float] = None) -> list[RawOutput]:
        floor = RAW_FLOOR if (threshold is None or self.filterable) else float(threshold)
        max_len = int(self.gcfg["max_length"])
        size, overlap = self._chunk_params()
        jobs, meta = [], []
        for i, text in enumerate(texts):
            ws = word_spans(text)
            n = len(ws)
            if self.long_text_strategy != "truncate" and n > size:
                wins = word_windows(n, size, overlap) if overlap else [(s, min(n, s + size)) for s in range(0, n, size)]
                for a, b in wins:
                    cs, ce = ws[a][0], ws[b - 1][1]
                    jobs.append((i, cs, text[cs:ce]))
                bounds = [ws[b - 1][1] for _, b in wins[:-1]]
                meta.append(TruncationInfo(strategy=self.long_text_strategy, max_supported=max_len, unit="words",
                                           input_length=n, truncated=False, num_chunks=len(wins),
                                           chunk_char_bounds=bounds))
            else:
                jobs.append((i, 0, text))
                truncated = n > max_len
                meta.append(TruncationInfo(strategy="truncate" if truncated else "none", max_supported=max_len,
                                           unit="words", input_length=n, truncated=truncated,
                                           visible_char_end=ws[max_len - 1][1] if truncated else None))
        results = self._run([j[2] for j in jobs], floor)
        per_text: list[list[dict]] = [[] for _ in texts]
        for (i, off, _), ents in zip(jobs, results):
            for e in ents:
                per_text[i].append({"start": e["start"] + off, "end": e["end"] + off,
                                    "label": e["label"], "score": float(e["score"])})
        return [RawOutput(payload=_resolve_overlaps(per_text[i]) if meta[i].num_chunks > 1 else per_text[i],
                          truncation=meta[i]) for i in range(len(texts))]

    def decode(self, raw: RawOutput, text: str, threshold: Optional[float]) -> list[Prediction]:
        thr = RAW_FLOOR if threshold is None else threshold
        if self.filterable and thr < RAW_FLOOR - 1e-9:
            raise ValueError(f"threshold {thr} below raw inference floor {RAW_FLOOR}")
        return [Prediction(start=e["start"], end=e["end"], text=text[e["start"]:e["end"]],
                           native_label=e["label"], canonical_label=self.mapping.to_canonical(e["label"]),
                           score=e["score"])
                for e in raw.payload if e["score"] >= thr - 1e-12]

    # ---- streaming (StreamingSpanGLiNER only) -----------------------------------------
    @property
    def supports_streaming(self) -> bool:
        return hasattr(getattr(self, "model", None), "clear_session")

    def infer_stream(self, chunks: list[str], threshold: float, session: str = "harness") -> list[Prediction]:
        """Feed chunks through a cached incremental session; return the final snapshot."""
        text = "".join(chunks)
        snap: list[dict] = []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with self._torch.inference_mode():
                for c in chunks:
                    if c:
                        snap = self.model.inference([c], self.labels, session_id=[session], threshold=threshold)[0]
        self.model.clear_session(session)
        return [Prediction(start=e["start"], end=e["end"], text=text[e["start"]:e["end"]], native_label=e["label"],
                           canonical_label=self.mapping.to_canonical(e["label"]), score=float(e["score"]))
                for e in snap]


def _resolve_overlaps(ents: list[dict]) -> list[dict]:
    """Merge chunk-overlap duplicates: greedy non-overlapping selection by score."""
    chosen: list[dict] = []
    for e in sorted(ents, key=lambda x: -x["score"]):
        if all(e["end"] <= c["start"] or e["start"] >= c["end"] for c in chosen):
            chosen.append(e)
    return sorted(chosen, key=lambda x: x["start"])
