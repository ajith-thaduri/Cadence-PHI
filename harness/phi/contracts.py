"""Canonical contracts of the PHI detection engine.

ONE object model is used by the Python API, the CLI, JSONL batch files, the tests and any
future service wrapper:

  DetectionRequest  -> engine.analyze() / engine.redact() -> DetectionResponse
  RedactionRequest  -> redaction.redact()                 -> RedactionResponse (+ TokenVault, kept separately)
  GoldDocument + DetectionResponse -> evaluation          -> EvaluationRecord   (evaluation only)

Coordinates: every span that leaves the engine (evidence, detections, redactions) is in
ORIGINAL request text characters, [start, end). Derived (normalised) views carry an offset
map; recognizers may read a derived view, but their evidence is projected back to original
offsets before anyone else sees it.

Scores: `raw_score` keeps the recognizer's own scale and `score_type` says what kind of
evidence it is. Nothing here is a probability unless calibration says so.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

ENGINE_VERSION = "phi-engine 1.0.0"
CONTRACT_VERSION = "1.0"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic opaque id (same inputs -> same id; reveals nothing about the text)."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{h}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ============================================================================ enums
class Modality(str, Enum):
    typed = "typed"
    note = "note"
    dictation = "dictation"
    telephone = "telephone"
    face_to_face = "face_to_face"
    asr = "asr"
    imported_text = "imported_text"


class Mode(str, Enum):
    DETECT = "detect"      # evidence + final decisions
    REDACT = "redact"      # + sanitized / tokenized text
    DEBUG = "debug"        # + full evidence chain, rejected candidates, trace, span text (local only)
    SCORE = "score"        # evaluation: identical engine path; scoring happens outside the engine


class ScoreType(str, Enum):
    model_confidence = "model_confidence"
    registry_exact = "registry_exact"
    registry_normalized = "registry_normalized"
    registry_fuzzy = "registry_fuzzy"
    regex_match = "regex_match"
    checksum_valid = "checksum_valid"
    contextual_rule = "contextual_rule"
    spoken_number_match = "spoken_number_match"


class EvidenceSource(str, Enum):
    rule = "rule"
    contextual_rule = "contextual_rule"
    registry = "registry"
    model = "model"
    external = "external"          # e.g. a Presidio predefined recognizer


class DecisionValue(str, Enum):
    REDACT = "REDACT"
    KEEP = "KEEP"
    REVIEW = "REVIEW"


class ConfidenceClass(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class MappingQuality(str, Enum):
    identity = "identity"            # derived text == original
    exact_segment = "exact_segment"  # every rewritten segment maps to an exact original segment;
                                     # spans inside a rewritten segment snap OUTWARD to its bounds
    approximate = "approximate"      # not usable for final redaction - supporting evidence only


class RejectionReason(str, Enum):
    below_threshold = "below_threshold"
    entity_disabled = "entity_disabled"
    recognizer_disabled = "recognizer_disabled"
    model_disabled = "model_disabled"
    normalizer_disabled = "normalizer_disabled"
    approximate_mapping = "approximate_mapping"
    allow_listed = "allow_listed"              # exact match of a configured non-PHI term (allow_list)
    shadow_mode = "shadow_mode"                # source runs in shadow: evidence recorded, never used for decisions
    non_phi = "non_phi"                        # span matches a configured non-PHI phrase / pattern (exclusions.yaml)


LANGUAGES = {"en", "es", "en-US", "es-US", "es-ES", "es-MX", "mixed", "auto"}


# ============================================================================ request
class RegistryContext(_Model):
    """Scoped references - NEVER raw database dumps. The registry recognizers resolve these
    through a RegistryStore inside the trust boundary."""
    subject_context_id: Optional[str] = None
    provider_directory_version: Optional[str] = None
    facility_directory_version: Optional[str] = None
    staff_directory_version: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _no_raw_records(cls, v: Any) -> Any:
        if isinstance(v, dict):
            bad = [k for k, x in v.items() if isinstance(x, (dict, list))]
            if bad:
                raise ValueError(f"registry_context must hold opaque references, not records: {bad}")
        return v


class DetectionOptions(_Model):
    mode: Mode = Mode.DETECT
    include_candidates: bool = False     # return all DetectionEvidence
    include_rejected: bool = False       # include rejected evidence (implies include_candidates for rejected)
    include_trace: bool = False
    include_text: bool = False           # raw span text in response (local evaluation / debug only)
    strict_logging: bool = True          # logs carry counts, ids, hashes - never span text
    redaction_operator: Optional[str] = None   # override policy default: REDACT | TOKENIZE | PSEUDONYMIZE | ENCRYPT

    @model_validator(mode="after")
    def _debug_defaults(self) -> "DetectionOptions":
        if self.mode == Mode.DEBUG:
            self.include_candidates = self.include_rejected = self.include_trace = True
        return self


class DetectionRequest(_Model):
    """`text` is the ORIGINAL input text (accepted as `text` or `original_text`); it is the
    canonical coordinate space and is never mutated."""
    model_config = ConfigDict(extra="forbid", use_enum_values=True, populate_by_name=True)
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(validation_alias=AliasChoices("text", "original_text"))
    language: str = "en"
    modality: Modality = Modality.typed
    source: Optional[str] = None
    normalization_profile: str = "default"
    enabled_entities: Optional[list[str]] = None
    enabled_recognizers: Optional[list[str]] = None
    enabled_models: Optional[list[str]] = None
    registry_context: Optional[RegistryContext] = None
    policy_profile: str = "cadence_default"
    options: DetectionOptions = Field(default_factory=DetectionOptions)
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)   # session / request metadata; must not hold PHI; never logged

    @property
    def original_text(self) -> str:
        return self.text

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in LANGUAGES:
            raise ValueError(f"unsupported language {v!r}; one of {sorted(LANGUAGES)}")
        return v

    @field_validator("request_id")
    @classmethod
    def _opaque(cls, v: str) -> str:
        if v.isdigit() and len(v) >= 6:
            raise ValueError("request_id must be opaque (it looks like an identifier, e.g. a member ID)")
        return v


# ============================================================================ text views
class OffsetSegment(_Model):
    derived_start: int
    derived_end: int
    original_start: int
    original_end: int
    transformation_id: Optional[str] = None     # None = unchanged (identity) segment


class TextView(_Model):
    view_id: str
    transformation_ids: list[str]
    mapping_quality: MappingQuality
    text: str = Field(repr=False)
    segments: list[OffsetSegment] = Field(default_factory=list, repr=False)

    def to_original(self, start: int, end: int) -> tuple[int, int, list[str]]:
        """Project a derived-text span onto ORIGINAL offsets (+ transformations it touched)."""
        if self.mapping_quality == MappingQuality.identity:
            return start, end, []
        os_, oe, used = None, None, []
        for g in self.segments:
            touches = (g.derived_start < end and start < g.derived_end) or \
                      (g.derived_start == g.derived_end and start <= g.derived_start < end)
            if not touches:
                continue
            if g.transformation_id is None:
                a = g.original_start + max(0, start - g.derived_start)
                b = g.original_start + min(g.derived_end, end) - g.derived_start
            else:
                a, b = g.original_start, g.original_end
                used.append(g.transformation_id)
            os_ = a if os_ is None else min(os_, a)
            oe = b if oe is None else max(oe, b)
        if os_ is None:
            return start, start, []
        return os_, oe, sorted(set(used))


class NormalizationTrace(_Model):
    normalizer_id: str
    version: str
    input_sha256: str
    output_sha256: str
    transformations: int
    mapping_quality: MappingQuality


class NormalizationInfo(_Model):
    profile: str
    normalizers_applied: list[str]
    traces: list[NormalizationTrace]
    text_views: list[dict[str, Any]]      # view_id, transformation_ids, mapping_quality (no text)
    mapping_status: Literal["ok", "approximate_views_evidence_only"]


# ============================================================================ evidence
class DetectionEvidence(_Model):
    evidence_id: str
    entity_type: str
    original_start: int
    original_end: int
    raw_score: float
    score_type: ScoreType
    recognizer_id: str
    recognizer_version: str
    model_id: Optional[str] = None
    model_revision: Optional[str] = None
    rule_id: Optional[str] = None
    source: EvidenceSource
    text_view_id: str = "original"
    threshold_used: Optional[float] = None       # filled by policy
    threshold_source: Optional[str] = None       # which config key supplied it
    accepted_as_candidate: Optional[bool] = None # filled by policy
    rejection_reason: Optional[RejectionReason] = None
    text: Optional[str] = None                   # only with options.include_text
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _span(self) -> "DetectionEvidence":
        if self.original_end <= self.original_start:
            raise ValueError("evidence span must be non-empty")
        return self


class RejectedEvidenceRef(_Model):
    evidence_id: str
    recognizer_id: str
    entity_type: str
    raw_score: float
    score_type: ScoreType
    threshold_used: Optional[float]
    reason: RejectionReason


# ============================================================================ final detection
class Detection(_Model):
    detection_id: str
    entity_type: str
    original_start: int
    original_end: int
    raw_text_available: bool                       # the span text exists in the request (always True for detections)
    text: Optional[str] = None                     # only with options.include_text
    redaction_action: Optional[str] = None         # operator the policy assigns (TOKENIZE / REDACT / ...); None for KEEP
    decision_score: float
    decision_score_type: str = "policy_aggregate"  # NOT a probability
    threshold: Optional[float]
    decision: DecisionValue
    confidence_class: ConfidenceClass
    decision_reason: str
    contributing_evidence_ids: list[str]
    contributing_recognizers: list[str]
    rejected_evidence: list[RejectedEvidenceRef] = Field(default_factory=list)
    policy_version: str
    reconciliation_version: str


# ============================================================================ response
class InputInfo(_Model):
    text_sha256: str
    text_length: int
    language: str
    resolved_language: str
    modality: str


class OutputInfo(_Model):
    mode: Mode
    sanitized_text: Optional[str] = None
    tokenized_text: Optional[str] = None
    operator: Optional[str] = None
    vault_id: Optional[str] = None                 # token map lives in the TokenVault, never here


class ResponseSummary(_Model):
    entities_detected: int
    entities_by_type: dict[str, int]
    decisions: dict[str, int]
    recognizers_used: list[str]
    recognizers_disabled: list[str]
    models_used: list[str]
    candidates: int
    candidates_accepted: int
    candidates_rejected: int
    overlap_groups: int
    shadow_only_spans: int = 0                     # shadow evidence that WOULD pass its threshold but no detection covers


class Timings(_Model):
    normalization_ms: float = 0.0
    recognizer_ms: float = 0.0
    model_ms: float = 0.0
    reconciliation_ms: float = 0.0
    policy_ms: float = 0.0
    redaction_ms: float = 0.0
    total_ms: float = 0.0
    per_recognizer_ms: dict[str, float] = Field(default_factory=dict)


class FailureInfo(_Model):
    """Why a request failed closed. Never contains exception text or PHI (NIST SP 800-53 SI-11)."""
    failure_class: str
    stage: str
    code: str
    action: Literal["block", "conservative_fallback", "reject"]
    retryable: bool = False


class Stamps(_Model):
    """Provenance stamped on every response: exactly which code, config and weights produced it."""
    engine_version: str = ENGINE_VERSION
    code_commit: str
    code_fingerprint: str
    config_sha256: str
    catalog_version: str
    models: dict[str, dict[str, Optional[str]]] = Field(default_factory=dict)   # key -> {revision, weights_sha256, mode}


class DetectionResponse(_Model):
    contract_version: str = CONTRACT_VERSION
    request_id: str
    engine_version: str = ENGINE_VERSION
    policy_version: str
    config_sha256: str
    input: InputInfo
    normalization: NormalizationInfo
    detections: list[Detection]
    evidence: list[DetectionEvidence] = Field(default_factory=list)
    output: OutputInfo
    summary: ResponseSummary
    trace: Optional[list[dict[str, Any]]] = None
    timings: Timings
    status: Literal["ok", "blocked", "fallback"] = "ok"   # blocked: caller MUST NOT forward the text
    failure: Optional[FailureInfo] = None
    stamps: Optional[Stamps] = None


# ============================================================================ redaction
class Operator(str, Enum):
    REDACT = "REDACT"            # "[NAME]" - irreversible
    TOKENIZE = "TOKENIZE"        # "[NAME_1]" - deterministic, reversible via TokenVault (Cadence default)
    PSEUDONYMIZE = "PSEUDONYMIZE"  # "NAME-7f3a9c" - deterministic keyed surrogate, reversible via TokenVault
    ENCRYPT = "ENCRYPT"          # AES ciphertext (Presidio anonymizer), reversible with the key


class RedactionRequest(_Model):
    request_id: str
    original_text: str
    detections: list[Detection]
    operator: Operator = Operator.TOKENIZE
    operators_by_entity: dict[str, Operator] = Field(default_factory=dict)
    redact_review: bool = True                     # REVIEW decisions are redacted (recall first)
    token_format: str = "[{ENTITY}_{N}]"            # from policy/redaction.yaml; must contain {ENTITY} and {N}
    coreference_entities: list[str] = Field(default_factory=list)   # redaction.name_coreference


class Replacement(_Model):
    detection_id: str
    entity_type: str
    original_start: int
    original_end: int
    output_start: int
    output_end: int
    operator: Operator
    replacement: str                               # the token / placeholder / ciphertext - never the PHI


class RedactionResponse(_Model):
    request_id: str
    status: Literal["ok", "blocked"] = "ok"            # blocked: sanitized_text is empty; do not forward anything
    failure: Optional[FailureInfo] = None
    sanitized_text: str
    operator: Operator
    replacements: list[Replacement]
    vault_id: Optional[str] = None
    original_sha256: str


# ============================================================================ gold / evaluation (evaluation only)
class GoldEntity(_Model):
    annotation_id: str
    entity_type: str
    subtype: Optional[str] = None
    expected_sources: list[str] = Field(default_factory=list)
    start: int
    end: int
    source: str = "authored"
    annotation_version: str = "1"
    ignore_region: bool = False                    # True: predictions here are neither TP nor FP
    edge_policy: Literal["exact", "trim_edge_function_words"] = "trim_edge_function_words"
    tags: list[str] = Field(default_factory=list)  # e.g. asr_altered


class GoldDocument(_Model):
    request_id: str
    dataset: str
    entities: list[GoldEntity]
    asr_lost: list[dict[str, Any]] = Field(default_factory=list)   # gold entities deleted/garbled by ASR (no span)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityOutcome(_Model):
    annotation_id: str
    entity_type: str
    start: Optional[int]
    end: Optional[int]
    outcome: Literal["detected_by_rule", "detected_by_registry", "detected_by_ml", "detected_by_multiple_sources",
                     "below_threshold", "no_candidate", "partial_span", "ASR_destroyed", "reconciliation_failure",
                     "policy_rejected", "disabled_only", "normalization_failure", "redaction_failure"]
    core_chars: int
    residual_chars: int                                  # core chars still visible in the REDACTED OUTPUT
    best_rejected: Optional[dict[str, Any]] = None
    failed_stage: Optional[str] = None                   # asr | configuration | policy | normalization | threshold |
                                                         # recognition | reconciliation | recognition_boundary | redaction
    detection_status: Literal["detected_full", "detected_partial", "not_detected", "asr_destroyed"] = "not_detected"
    redaction_status: Literal["redacted_ok", "redaction_failure", "n/a"] = "n/a"
    sources: list[str] = Field(default_factory=list)     # DETERMINISTIC | CONVERSATIONAL | REGISTRY | MODEL:<key> | PRESIDIO
    sole_catch: Optional[str] = None                     # the only source group that protected it
    subtype: Optional[str] = None


class EvaluationRecord(_Model):
    request_id: str
    dataset: str
    response_ref: str                              # predictions.jsonl line / response sha
    core_chars: int
    residual_chars: int
    residual_fraction: float
    tp_entities: int
    fn_entities: int
    fp_detections: int
    over_redacted_chars: int
    sanitized: bool
    outcomes: list[EntityOutcome]
