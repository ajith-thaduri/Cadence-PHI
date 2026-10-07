"""API request / response models (the OpenAPI schema the frontend's types are generated from).

No response model has a field for ORIGINAL text: only offsets, decisions, metadata and sanitized output.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["typed", "note", "dictation", "telephone", "face_to_face", "asr", "imported_text"]
OperatorName = Literal["REDACT", "TOKENIZE", "PSEUDONYMIZE", "ENCRYPT"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------- inputs
class KnownDetails(_M):
    """What the caller already knows about the member - used as a scoped registry for ONE request, then discarded."""
    name: Optional[str] = Field(None, max_length=200)
    dob: Optional[str] = Field(None, max_length=20, description="YYYY-MM-DD")
    member_ids: list[str] = Field(default_factory=list, max_length=20)
    mrn: Optional[str] = Field(None, max_length=64)
    phone: Optional[str] = Field(None, max_length=64)
    email: Optional[str] = Field(None, max_length=200)
    address: Optional[str] = Field(None, max_length=300)
    city: Optional[str] = Field(None, max_length=100)
    zip: Optional[str] = Field(None, max_length=20)
    contacts: list[str] = Field(default_factory=list, max_length=50, description="relatives / caregivers")
    providers: list[str] = Field(default_factory=list, max_length=50)
    facilities: list[str] = Field(default_factory=list, max_length=50)
    staff: list[str] = Field(default_factory=list, max_length=50)
    employers: list[str] = Field(default_factory=list, max_length=20)

    def is_empty(self) -> bool:
        return not any(v for v in self.model_dump().values())

    def to_registry(self) -> dict[str, Any]:
        m = {k: v for k, v in {"name": self.name, "dob": self.dob, "member_ids": [x for x in self.member_ids if x],
                               "mrn": self.mrn, "phone": self.phone, "email": self.email, "address": self.address,
                               "city": self.city, "zip": self.zip}.items() if v}
        reg: dict[str, Any] = {"member": m}
        for k in ("contacts", "staff", "employers"):
            if getattr(self, k):
                reg[k] = [x for x in getattr(self, k) if x]
        if self.providers:
            reg["providers"] = [{"name": x} for x in self.providers if x]
        if self.facilities:
            reg["facilities"] = [{"name": x} for x in self.facilities if x]
        return reg


class WhatIf(_M):
    """Non-persistent overrides for one analysis. Never written to configs/phi."""
    disable_entities: list[str] = Field(default_factory=list)
    enable_entities: list[str] = Field(default_factory=list)
    disable_recognizers: list[str] = Field(default_factory=list)
    models: Optional[list[str]] = Field(None, description="null = configured models; [] = no contextual model")
    thresholds: dict[str, float] = Field(default_factory=dict, description="'entity:NAME' or 'model:obi_roberta' -> 0..1")

    def is_empty(self) -> bool:
        return not (self.disable_entities or self.enable_entities or self.disable_recognizers or self.thresholds
                    or self.models is not None)


class AnalyzeIn(_M):
    text: str = Field(min_length=1)
    modality: Modality = "typed"
    normalization_profile: Optional[str] = Field(None, description="default: asr for telephone/asr/face_to_face, else default")
    policy_profile: str = "cadence_default"
    operator: Optional[OperatorName] = None
    conversation_id: Optional[str] = Field(None, max_length=128, pattern=r"^[A-Za-z0-9_\-:.]*$")
    known: Optional[KnownDetails] = None
    what_if: Optional[WhatIf] = None
    run_guard: bool = True
    include_evidence: bool = False


class RestoreIn(_M):
    vault_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{4,128}$")
    text: str = Field(min_length=1, max_length=200_000)


class LoginIn(_M):
    username: str = Field("", max_length=64)
    secret: str = Field(min_length=1, max_length=512)


class JobIn(_M):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = Field(None, max_length=500, description="required for runs on test data")


# ---------------------------------------------------------------------------- outputs
class RejectedOut(_M):
    recognizer_id: str
    entity_type: str
    raw_score: float
    score_type: str
    threshold_used: Optional[float]
    reason: str


class DetectionOut(_M):
    id: str
    entity_type: str
    start: int
    end: int
    decision: str
    confidence_class: str
    decision_score: float
    threshold: Optional[float]
    reason: str
    recognizers: list[str]
    sources: list[str]
    action: Optional[str]
    rejected: list[RejectedOut]


class EvidenceOut(_M):
    id: str
    entity_type: str
    start: int
    end: int
    raw_score: float
    score_type: str
    recognizer_id: str
    source: str
    view: str
    threshold_used: Optional[float]
    threshold_source: Optional[str]
    accepted: Optional[bool]
    rejection_reason: Optional[str]
    metadata: dict[str, Any]


class GuardHitOut(_M):
    entity_type: str
    start: int
    end: int
    recognizers: list[str]
    blocking: bool


class GuardOut(_M):
    allowed: bool
    status: str
    hits: list[GuardHitOut]


class AnalyzeOut(_M):
    request_id: str
    status: str
    failure: Optional[dict[str, Any]]
    sanitized_text: Optional[str]
    operator: Optional[str]
    vault_id: Optional[str]
    detections: list[DetectionOut]
    evidence: list[EvidenceOut]
    summary: dict[str, Any]
    timings: dict[str, Any]
    guard: Optional[GuardOut]
    what_if: bool
    notices: list[str]
    config_sha256: str
    stamps: Optional[dict[str, Any]]


class RestoreOut(_M):
    text: str
    replaced: int


class JobOut(_M):
    id: str
    type: str
    title: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    params: dict[str, Any]
    commands: list[str]
    created: str
    started: Optional[str] = None
    finished: Optional[str] = None
    exit_code: Optional[int] = None
    user: str
    output: Optional[str] = None
    run_ref: Optional[dict[str, str]] = None
    progress: Optional[dict[str, int]] = None
    error: Optional[str] = None


class Principal(_M):
    name: str
    role: str
