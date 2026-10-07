"""Common adapter interface.

Every adapter separates *inference* (`infer_raw`, expensive, run once) from *decoding*
(`decode`, cheap, run per threshold). This lets the threshold sweep reuse one forward
pass per document without changing what the model saw.
"""
from __future__ import annotations

import logging
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from harness.config import model_entry
from harness.label_mapping import LabelMapping
from harness.schema import Prediction, TruncationInfo

log = logging.getLogger(__name__)


class ModelStatus(str, Enum):
    NOT_LOADED = "NOT_LOADED"
    LOADED = "LOADED"
    FAILED_TO_LOAD = "FAILED TO LOAD"


@dataclass
class RawOutput:
    payload: Any
    truncation: TruncationInfo


class BaseModelAdapter(ABC):
    supports_scores: bool = True
    # True when decode(raw, t) at a higher threshold is exactly a filter of a low-threshold
    # inference. False (e.g. token-level GLiNER) means the runner re-infers per threshold.
    filterable: bool = True
    STRATEGIES = ("author", "production", "naive", "truncate")

    def __init__(self, key: str, device: str, long_text_strategy: str = "author") -> None:
        self.key = key
        self.entry = model_entry(key)
        self.mapping = LabelMapping.load(key)
        self.device = device
        if long_text_strategy == "chunk":
            long_text_strategy = "author"
        if long_text_strategy not in self.STRATEGIES:
            raise ValueError(f"unknown long_text_strategy {long_text_strategy!r}")
        self.long_text_strategy = long_text_strategy
        self.status = ModelStatus.NOT_LOADED
        self.load_error: Optional[str] = None
        self.load_traceback: Optional[str] = None
        self.load_time_s: Optional[float] = None
        self.runtime_notes: list[str] = []

    # ---- lifecycle -------------------------------------------------------------------
    def load(self) -> bool:
        """Load the checkpoint. Never raises: failures are recorded as FAILED TO LOAD."""
        t0 = time.perf_counter()
        try:
            self._load()
            self.status = ModelStatus.LOADED
        except Exception as exc:  # noqa: BLE001
            self.status = ModelStatus.FAILED_TO_LOAD
            self.load_error = f"{type(exc).__name__}: {exc}"
            self.load_traceback = traceback.format_exc()
            log.error("%s FAILED TO LOAD: %s", self.key, self.load_error)
        self.load_time_s = time.perf_counter() - t0
        return self.status == ModelStatus.LOADED

    @abstractmethod
    def _load(self) -> None: ...

    def unload(self) -> None:
        import gc

        import torch

        for attr in ("model", "tokenizer"):
            if hasattr(self, attr):
                setattr(self, attr, None)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    # ---- inference -------------------------------------------------------------------
    @property
    @abstractmethod
    def default_threshold(self) -> Optional[float]:
        """Operating point documented by the model authors (None = argmax decoding)."""

    @abstractmethod
    def infer_raw(self, text: str, threshold: Optional[float] = None) -> RawOutput: ...

    def infer_raw_batch(self, texts: list[str], threshold: Optional[float] = None) -> list[RawOutput]:
        return [self.infer_raw(t, threshold) for t in texts]

    @abstractmethod
    def decode(self, raw: RawOutput, text: str, threshold: Optional[float]) -> list[Prediction]: ...

    def predict(self, text: str, threshold: Optional[float] = "default") -> list[Prediction]:  # type: ignore[assignment]
        thr = self.default_threshold if threshold == "default" else threshold
        return self.decode(self.infer_raw(text, None if self.filterable else thr), text, thr)

    def predict_batch(self, texts: list[str], threshold: Optional[float] = "default") -> list[list[Prediction]]:  # type: ignore[assignment]
        thr = self.default_threshold if threshold == "default" else threshold
        raws = self.infer_raw_batch(texts, None if self.filterable else thr)
        return [self.decode(r, t, thr) for r, t in zip(raws, texts)]

    # ---- metadata --------------------------------------------------------------------
    def get_native_labels(self) -> list[str]:
        return self.mapping.native_labels

    @abstractmethod
    def max_input_length(self) -> tuple[int, str]:
        """(limit, unit) of a single forward pass."""

    def get_metadata(self) -> dict[str, Any]:
        e = self.entry
        limit, unit = self.max_input_length()
        return {
            "key": self.key,
            "display_name": e["display_name"],
            "model_id": e["model_id"],
            "revision": e.get("revision"),
            "adapter": e["adapter"],
            "architecture": e.get("architecture"),
            "parameter_count_registry": e.get("parameter_count"),
            "parameter_count_measured": self._param_count(),
            "license": e.get("license"),
            "native_labels": self.get_native_labels(),
            "label_provenance": self.mapping.provenance,
            "max_input_length": limit,
            "max_input_unit": unit,
            "long_text_strategy": self.long_text_strategy,
            "filterable_thresholds": self.filterable,
            "evaluation_role": e.get("evaluation_role"),
            "provenance_status": e.get("provenance_status"),
            "default_threshold": self.default_threshold,
            "supports_scores": self.supports_scores,
            "device": self.device,
            "status": self.status.value,
            "load_error": self.load_error,
            "load_time_s": self.load_time_s,
            "model_file_bytes": self.model_file_bytes(),
            "runtime_notes": self.runtime_notes,
        }

    def _param_count(self) -> Optional[int]:
        m = getattr(self, "model", None)
        if m is None:
            return None
        try:
            return int(sum(p.numel() for p in m.parameters()))
        except Exception:  # noqa: BLE001
            return None

    def model_file_bytes(self) -> Optional[int]:
        """Size of the cached weight files for the pinned revision (no network)."""
        try:
            from huggingface_hub import scan_cache_dir

            for repo in scan_cache_dir().repos:
                if repo.repo_id == self.entry["model_id"]:
                    for rev in repo.revisions:
                        if rev.commit_hash == self.entry.get("revision"):
                            return int(sum(f.size_on_disk for f in rev.files
                                           if f.file_name.endswith((".bin", ".safetensors"))))
        except Exception:  # noqa: BLE001
            return None
        return None
