"""ModelRecognizerAdapter: one clinical NER model behind the recognizer contract.

The model only proposes evidence (span + native label + raw confidence) down to its candidate
floor; thresholds, reconciliation and redaction happen elsewhere. Adding a model = an entry in
configs/models.yaml (adapter type, pinned revision, label mapping) + configs/phi_harness.yaml
`models:`; no evaluation or engine code changes.

`cache` (text sha256 -> RawHits) lets evaluation reuse inference across configurations without
changing what the engine does; it is keyed by model revision and candidate floor by the caller. A serving
engine gets a bounded HitCache instead (runtime.model_cache.max_entries; 0 in every shipped environment):
an unbounded per-text cache is a memory leak and keeps PHI-derived spans beyond the request.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Optional

from harness.adapters.base import ModelStatus
from harness.phi.contracts import EvidenceSource, ScoreType, sha256
from harness.phi.recognizers.base import PHIRecognizer, RawHit


class HitCache:
    """Thread-safe bounded LRU: text sha256 -> RawHits. max_entries 0 = nothing is retained after the request."""

    def __init__(self, max_entries: int = 0) -> None:
        self.max_entries = int(max_entries)
        self._d: OrderedDict[str, list[RawHit]] = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._d

    def __getitem__(self, key: str) -> list[RawHit]:
        with self._lock:
            self._d.move_to_end(key)
            return self._d[key]

    def __setitem__(self, key: str, value: list[RawHit]) -> None:
        if self.max_entries <= 0:
            return
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.max_entries:
                self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


class ModelRecognizerAdapter(PHIRecognizer):
    source = EvidenceSource.model

    def __init__(self, model_key: str, adapter: Any, candidate_floor: float = 0.1, language: str = "en",
                 cache: Optional[dict[str, list[RawHit]]] = None) -> None:
        ents = sorted({adapter.mapping.to_canonical(n) for n in adapter.mapping.native_labels} - {"UNMAPPED"})
        super().__init__(f"model_{model_key}", ents, language, version=str(adapter.entry.get("revision", ""))[:12])
        self.model_key = model_key
        self.adapter = adapter
        self.floor = float(candidate_floor)
        self.model_id = adapter.entry.get("model_id")
        self.model_revision = adapter.entry.get("revision")
        self.cache = cache if cache is not None else {}

    @property
    def metadata(self) -> dict[str, Any]:
        limit, unit = self.adapter.max_input_length()
        return {"model_id": self.model_id, "model_revision": self.model_revision, "model_type": self.adapter.entry.get("adapter"),
                "supported_entities": self.supported_entities,
                "tokenizer": self.adapter.entry.get("tokenizer_id") or self.model_id,
                "input_limit": f"{limit} {unit}", "runtime_device": self.adapter.device, "candidate_floor": self.floor}

    def hits(self, text: str) -> list[RawHit]:
        key = sha256(text)
        try:
            return self.cache[key]
        except KeyError:
            pass
        a = self.adapter
        if getattr(a, "status", ModelStatus.LOADED) != ModelStatus.LOADED:
            if not a.load():   # lazy: evaluation may run entirely from cache without loading weights
                raise RuntimeError(f"model {self.model_key} failed to load: {a.load_error}")
        raw = a.infer_raw(text, None if a.filterable else self.floor)
        out = []
        for p in a.decode(raw, text, self.floor):
            if p.canonical_label == "UNMAPPED":
                continue
            out.append(RawHit(p.canonical_label, p.start, p.end, float(p.score) if p.score is not None else 1.0,
                              ScoreType.model_confidence, metadata={"native_label": p.native_label,
                                                                    "chunks": raw.truncation.num_chunks}))
        self.cache[key] = out
        return out
