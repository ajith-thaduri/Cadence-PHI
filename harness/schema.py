"""Data schemas shared across the harness."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from harness.taxonomy import CANONICAL_LABELS


class GoldEntity(BaseModel):
    start: int
    end: int
    text: str
    label: str
    difficulty: str = "medium"           # easy | medium | hard
    tags: list[str] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _canonical(cls, v: str) -> str:
        if v not in CANONICAL_LABELS:
            raise ValueError(f"gold label {v!r} is not in the canonical taxonomy")
        return v


class IgnoreSpan(BaseModel):
    """Region where predictions are neither TP nor FP (e.g. ages, out-of-taxonomy gold)."""
    start: int
    end: int
    reason: str


class Sample(BaseModel):
    id: str
    text: str
    entities: list[GoldEntity]
    ignore_spans: list[IgnoreSpan] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def validate_offsets(self) -> None:
        for e in self.entities:
            if self.text[e.start:e.end] != e.text:
                raise ValueError(
                    f"{self.id}: offset mismatch for {e.label} [{e.start}:{e.end}] "
                    f"{self.text[e.start:e.end]!r} != {e.text!r}"
                )

    @property
    def subset(self) -> str:
        return str(self.metadata.get("subset", "UNKNOWN"))

    @property
    def language(self) -> str:
        return str(self.metadata.get("language", "unknown"))

    @property
    def source(self) -> str:
        return str(self.metadata.get("source", "unknown"))


class Prediction(BaseModel):
    """Common prediction schema returned by every adapter."""
    start: int
    end: int
    text: str
    native_label: str
    canonical_label: str
    score: Optional[float] = None       # None when the model emits no confidence


class TruncationInfo(BaseModel):
    """What part of the input the model actually saw."""
    strategy: str                        # chunk | truncate | none
    max_supported: int                   # in model units (tokens or words)
    unit: str                            # "wordpiece_tokens" | "words"
    input_length: int                    # in the same unit
    truncated: bool
    visible_char_end: Optional[int] = None   # chars >= this were never seen (truncate mode)
    num_chunks: int = 1
    chunk_char_bounds: list[int] = []        # char offsets where one chunk's scored region ends
