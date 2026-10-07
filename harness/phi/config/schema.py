"""Strict schema for the resolved engine configuration (fail closed on any invalid config).

Every section is a Pydantic model with extra="forbid": unknown keys, typos, out-of-range thresholds
and references to unknown entities / recognizers / normalizers / models stop the engine from
starting (Presidio's own provider silently falls back to defaults - we never do).
"""
from __future__ import annotations

import re

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Operator = Literal["TOKENIZE", "REDACT", "PSEUDONYMIZE", "ENCRYPT"]
ScoreTypeName = Literal["model_confidence", "registry_exact", "registry_normalized", "registry_fuzzy", "regex_match",
                        "checksum_valid", "contextual_rule", "spoken_number_match"]
Modality = Literal["typed", "note", "dictation", "telephone", "face_to_face", "asr", "imported_text"]
SAFE_HARBOR = {"names", "geographic", "dates", "age_over_89", "telephone", "fax", "email", "ssn", "mrn",
               "health_plan_beneficiary", "account", "certificate_license", "vehicle", "device", "url", "ip_address",
               "biometric", "photo", "other_unique_id"}


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _thr(v: Optional[float]) -> Optional[float]:
    if v is not None and not (0.0 <= float(v) <= 1.0):
        raise ValueError(f"threshold {v} outside [0, 1]")
    return v


# ------------------------------------------------------------------------------------------ catalog
class CoreChars(_S):
    strip_titles: bool | list[str] = True
    strip_affixes: list[str] = Field(default_factory=list)
    strip_edge_words: bool = True


class EntityEvaluation(_S):
    high_risk: bool = False
    gate_group: Literal["direct_identifiers", "quasi_identifiers", "contextual", "none"] = "none"


class EntitySpec(_S):
    enabled: bool = True
    enabled_default: Optional[bool] = None
    description: str = ""
    category: Literal["person", "contact", "location", "date", "identifier", "organization", "other"] = "other"
    hipaa_safe_harbor: list[str] = Field(default_factory=list)
    risk_tier: Literal["direct", "quasi", "contextual"] = "contextual"
    default_action: Optional[Operator] = None
    subtypes: list[str] = Field(default_factory=list)
    core_chars: CoreChars = Field(default_factory=CoreChars)
    outbound_guard: Literal["block", "flag", "off"] = "off"
    evaluation: EntityEvaluation = Field(default_factory=EntityEvaluation)

    @field_validator("hipaa_safe_harbor")
    @classmethod
    def _sh(cls, v: list[str]) -> list[str]:
        bad = set(v) - SAFE_HARBOR
        if bad:
            raise ValueError(f"unknown Safe Harbor identifier(s) {sorted(bad)}; known: {sorted(SAFE_HARBOR)}")
        return v


# ------------------------------------------------------------------------------------------ recognizers
class _RecBase(_S):
    enabled: bool = False
    enabled_default: Optional[bool] = None
    version: str = "1"
    developed_on: list[str] = Field(default_factory=list)
    evaluated_on_dev: Optional[str] = None
    mode: Literal["production", "shadow"] = "production"


class RuleFamilySpec(_RecBase):
    family: str
    emits: list[str] = Field(default_factory=list)


class ContextualSpec(_RecBase):
    contextual: Literal[True]
    function: Optional[str] = None
    emits: list[str] = Field(default_factory=list)


class RegistrySpec(_RecBase):
    kinds: list[str]


class PresidioSpec(_RecBase):
    presidio_class: str
    entity_map: dict[str, str]


class PatternDef(_S):
    name: str
    regex: str
    group: int = 0
    score: float = 1.0
    flags: list[Literal["IGNORECASE", "MULTILINE"]] = Field(default_factory=list)
    requires_context: bool = False


class PatternContext(_S):
    words: list[str] = Field(default_factory=list)
    window: int = 40
    required: bool = False
    negative_words: list[str] = Field(default_factory=list)
    side: Literal["left", "both"] = "left"          # field labels precede values; "both" also looks right
    max_words_between: Optional[int] = Field(None, ge=0, le=10)   # cue must sit this close to the value


class PatternSpec(_RecBase):
    type: Literal["pattern"]
    entity: str
    patterns: list[PatternDef]
    validators: list[str] = Field(default_factory=list)
    context: PatternContext = Field(default_factory=PatternContext)


class LabelledSpec(_RecBase):
    """Field label -> typed identifier ("DEA registration TR5521408", "IBAN GB29...", "bank account #0041...")."""
    type: Literal["labelled"]
    labels: dict[str, list[str]]                     # entity -> label regexes (case-insensitive, word-bounded)
    bridge: str                                      # what may sit between label and value
    value: str                                       # one value token
    min_digits: int = Field(3, ge=0)
    min_length: int = Field(5, ge=1)
    score: float = 0.9
    continuation: Optional[str] = None               # a short non-digit first token may continue ("NV 4KL-221")

    @field_validator("bridge", "value", "continuation")
    @classmethod
    def _rx(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            re.compile(v)
        return v

    @model_validator(mode="after")
    def _labels(self) -> "LabelledSpec":
        for ent, lbls in self.labels.items():
            for l in lbls:
                re.compile(l)
        return self


RecognizerSpec = Union[PatternSpec, LabelledSpec, ContextualSpec, RegistrySpec, PresidioSpec, RuleFamilySpec]


def parse_recognizer(rid: str, raw: dict[str, Any]) -> BaseModel:
    if raw.get("type") == "pattern":
        return PatternSpec(**raw)
    if raw.get("type") == "labelled":
        return LabelledSpec(**raw)
    if raw.get("contextual"):
        return ContextualSpec(**raw)
    if "kinds" in raw:
        return RegistrySpec(**raw)
    if "presidio_class" in raw:
        return PresidioSpec(**raw)
    if "family" in raw:
        return RuleFamilySpec(**raw)
    raise ValueError(f"recognizer {rid!r}: cannot determine type (needs type: pattern | contextual | kinds | "
                     f"presidio_class | family)")


# ------------------------------------------------------------------------------------------ other sections
class ModelSpec(_S):
    enabled: bool = False
    candidate_floor: float = 0.1
    mode: Literal["production", "shadow"] = "production"
    weights_sha256: Optional[str] = None
    device: Optional[str] = None

    @field_validator("candidate_floor")
    @classmethod
    def _floor(cls, v: float) -> float:
        return _thr(v)


class ModalityThresholds(_S):
    entity: dict[str, float] = Field(default_factory=dict)
    model_entity: dict[str, dict[str, float]] = Field(default_factory=dict)


class Thresholds(_S):
    default: float = 0.5
    deterministic_default: float = 0.0
    applies_to_score_types: list[ScoreTypeName] = Field(default_factory=lambda: ["model_confidence"])
    recognizer: dict[str, float] = Field(default_factory=dict)
    model: dict[str, float] = Field(default_factory=dict)
    entity: dict[str, float] = Field(default_factory=dict)
    model_entity: dict[str, dict[str, float]] = Field(default_factory=dict)
    modality: dict[Modality, ModalityThresholds] = Field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ranges(self) -> "Thresholds":
        vals = [self.default, self.deterministic_default, *self.recognizer.values(), *self.model.values(), *self.entity.values()]
        for per in self.model_entity.values():
            vals += list(per.values())
        for m in self.modality.values():
            vals += list(m.entity.values())
            for per in m.model_entity.values():
                vals += list(per.values())
        for v in vals:
            _thr(v)
        return self


class NormalizerSpec(_S):
    enabled: bool = True
    version: str = "1"


class Confidence(_S):
    high_score_types: list[ScoreTypeName] = Field(default_factory=list)
    medium_margin: float = 0.2


class PolicyProfile(_S):
    operator: Operator = "TOKENIZE"
    review_band: float = 0.0
    redact_review: bool = True
    confidence: Confidence = Field(default_factory=Confidence)


class VaultSpec(_S):
    backend: Literal["memory", "encrypted_file"] = "memory"
    path: str = "vault"
    key_ref: str = "env:PHI_VAULT_KEY"
    ttl_days: int = Field(30, ge=1, le=3650)
    max_vaults: int = Field(10_000, ge=1)        # memory backend bound (LRU)
    audit: bool = True


class RedactionSpec(_S):
    default_operator: Operator = "TOKENIZE"
    token_format: str = "[{ENTITY}_{N}]"
    consistency_scope: Literal["document", "conversation", "member"] = "document"
    name_coreference: list[str] = Field(default_factory=list)   # entity types whose partial mentions reuse a full name's token
    per_entity: dict[str, Operator] = Field(default_factory=dict)
    vault: VaultSpec = Field(default_factory=VaultSpec)

    @field_validator("token_format")
    @classmethod
    def _fmt(cls, v: str) -> str:
        if "{ENTITY}" not in v or "{N}" not in v:
            raise ValueError("token_format must contain {ENTITY} and {N}")
        return v


class SelfTestCase(_S):
    text: str
    language: str = "en"
    normalization_profile: str = "default"
    entities: list[str] = Field(default_factory=list)
    must_redact: list[str] = Field(default_factory=list)


class SelfTest(_S):
    required: bool = True
    cases: list[SelfTestCase] = Field(default_factory=list)


class CanaryCase(_S):
    id: str
    text: str
    entities: list[str] = Field(default_factory=list)
    must_redact: list[str] = Field(default_factory=list)
    must_not_redact: bool = False
    normalization_profile: str = "default"
    modality: str = "typed"
    language: str = "en"


class CanarySpec(_S):
    interval_seconds: int = Field(300, ge=10)
    cases: list[CanaryCase] = Field(default_factory=list)


class DetectionRateSpec(_S):
    baseline: str = "results/monitoring/baseline.json"
    control_limit_sigma: float = Field(3.0, gt=0)
    min_chars_per_window: int = Field(20_000, ge=0)
    window_minutes: int = Field(60, ge=1)


class MonitoringSpec(_S):
    canaries: CanarySpec = Field(default_factory=CanarySpec)
    detection_rate: DetectionRateSpec = Field(default_factory=DetectionRateSpec)


class ReconciliationSpec(_S):
    split_bridged_groups: bool = False
    connector_words: list[str] = Field(default_factory=list)
    field_labels: list[str] = Field(default_factory=list)
    fragment_joiners: str = ""
    abbreviation_joiners: list[str] = Field(default_factory=list)   # "St. Vincent", "Mt. Sinai" (+ any single initial)


class ContextTypeRule(_S):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    from_types: list[str] = Field(alias="from", min_length=1)
    to: str
    cues: list[str] = Field(min_length=1)
    window: int = Field(40, ge=5, le=200)
    bridge: Optional[str] = None
    unless_preceded_by: Optional[str] = None
    # mode cue: a cue BEFORE the span refines from -> to.  mode requires: a span typed `from` by the model ALONE keeps
    # that type only if a cue appears within `window` on either side; otherwise it becomes `to`
    mode: Literal["cue", "requires"] = "cue"

    @field_validator("bridge", "unless_preceded_by")
    @classmethod
    def _rx(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            re.compile(v)
        return v


class NonPhiPhrase(_S):
    id: str
    reason: str
    terms: list[str] = Field(min_length=1)
    # a phrase = optional model year + term + up to `max_tail_tokens` capitalised / alphanumeric tokens
    max_tail_tokens: int = Field(2, ge=0, le=4)
    require_year_or_tail: bool = True              # a bare term ("Ford", "Lincoln") is usually a person or place
    cues: list[str] = Field(default_factory=list)  # one must appear within `window` chars on either side
    window: int = Field(40, ge=0, le=200)


class ModelCacheSpec(_S):
    max_entries: int = Field(0, ge=0, le=100_000)


class FailureSpec(_S):
    on_model_error: Literal["block", "conservative_fallback"] = "block"
    on_recognizer_error: Literal["block"] = "block"
    on_timeout: Literal["block", "conservative_fallback"] = "block"
    on_oversize_input: Literal["reject"] = "reject"
    on_unsupported_language: Literal["reject"] = "reject"
    on_registry_unavailable: Literal["block"] = "block"
    on_vault_unavailable: Literal["block"] = "block"
    on_key_unavailable: Literal["block"] = "block"
    on_redaction_error: Literal["block"] = "block"
    on_internal_error: Literal["block"] = "block"
    deadline_ms: dict[Literal["total", "model"], float] = Field(default_factory=lambda: {"total": 30000, "model": 20000})
    max_input_chars: int = 50000
    startup_self_test: SelfTest = Field(default_factory=SelfTest)


class OutboundGuardSpec(_S):
    enabled: bool = True
    recognizers: list[str] = Field(default_factory=list)       # empty = all enabled rule + registry recognizers
    action: Literal["block", "flag"] = "block"


class RegistryMatching(_S):
    fuzzy: bool = True
    fuzzy_min_len: int = 5
    fuzzy_max_edits: int = 1
    fuzzy_max_edits_long: int = 2
    fuzzy_long_len: int = 8
    single_token_requires_capital: bool = True
    partial_token_min_len: int = 3
    nicknames: dict[str, list[str]] = Field(default_factory=dict)
    particles: list[str] = Field(default_factory=list)
    common_words: list[str] = Field(default_factory=list)


class RegistrySection(_S):
    fuzzy: bool = True
    single_token_requires_capital: bool = True
    directories_path: str = "data/registry"
    kind_entities: dict[str, list[str]] = Field(default_factory=dict)
    matching: Optional[RegistryMatching] = None


class ResolvedConfig(_S):
    """The whole resolved configuration (all files + overlays composed)."""
    version: int
    catalog_version: str = "legacy"
    engine: dict[str, str]
    languages: dict[str, Any]
    entities: dict[str, EntitySpec]
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    thresholds: Thresholds
    precedence: list[ScoreTypeName]
    allow_list: list[str] = Field(default_factory=list)
    non_phi_phrases: list[NonPhiPhrase] = Field(default_factory=list)
    non_phi_patterns: dict[str, list[str]] = Field(default_factory=dict)   # entity -> full-span regexes never PHI
    non_phi_pattern_modalities: list[str] = Field(default_factory=list)    # empty = every modality
    type_refinements: dict[str, list[str]] = Field(default_factory=dict)
    recognizers: dict[str, dict[str, Any]]
    contextual_params: dict[str, Any] = Field(default_factory=dict)
    registry: RegistrySection = Field(default_factory=RegistrySection)
    models: dict[str, ModelSpec]
    normalizers: dict[str, NormalizerSpec]
    normalized_views_feed: list[Literal["rule", "contextual_rule", "registry"]] = Field(
        default_factory=lambda: ["rule", "contextual_rule", "registry"])
    normalization_profiles: dict[str, list[str]]
    policy_profiles: dict[str, PolicyProfile]
    redaction: RedactionSpec = Field(default_factory=RedactionSpec)
    failure: FailureSpec = Field(default_factory=FailureSpec)
    outbound_guard: OutboundGuardSpec = Field(default_factory=OutboundGuardSpec)
    presidio: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    presets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    environment: str = "base"
    apply_generated: bool = True
    telemetry: dict[str, Any] = Field(default_factory=dict)
    model_cache: ModelCacheSpec = Field(default_factory=lambda: ModelCacheSpec())
    reconciliation: ReconciliationSpec = Field(default_factory=lambda: ReconciliationSpec())
    contextual_type_rules: list[ContextTypeRule] = Field(default_factory=list)
    monitoring: MonitoringSpec = Field(default_factory=lambda: MonitoringSpec())

    @model_validator(mode="after")
    def _integrity(self) -> "ResolvedConfig":
        ents = set(self.entities)
        errs: list[str] = []

        def need(eid: str, where: str) -> None:
            if eid not in ents:
                errs.append(f"{where}: unknown entity {eid!r}")
        for a, members in self.aliases.items():
            for e in members:
                need(e, f"aliases.{a}")
        for k, subs in self.type_refinements.items():
            need(k, "type_refinements")
            for e in subs:
                need(e, f"type_refinements.{k}")
        t = self.thresholds
        for e in t.entity:
            need(e, "thresholds.entity")
        for m, per in t.model_entity.items():
            if m not in self.models:
                errs.append(f"thresholds.model_entity: unknown model {m!r}")
            for e in per:
                need(e, f"thresholds.model_entity.{m}")
        for m in t.model:
            if m not in self.models:
                errs.append(f"thresholds.model: unknown model {m!r}")
        for mod, mt in t.modality.items():
            for e in mt.entity:
                need(e, f"thresholds.modality.{mod}.entity")
            for m, per in mt.model_entity.items():
                if m not in self.models:
                    errs.append(f"thresholds.modality.{mod}.model_entity: unknown model {m!r}")
                for e in per:
                    need(e, f"thresholds.modality.{mod}.model_entity.{m}")
        for rid in t.recognizer:
            if rid not in self.recognizers and not rid.startswith("model_"):
                errs.append(f"thresholds.recognizer: unknown recognizer {rid!r}")
        for rid, raw in self.recognizers.items():
            try:
                spec = parse_recognizer(rid, raw)
            except Exception as exc:  # noqa: BLE001
                errs.append(f"recognizers.{rid}: {exc}")
                continue
            for e in getattr(spec, "emits", []) or []:
                need(e, f"recognizers.{rid}.emits")
            if isinstance(spec, PatternSpec):
                need(spec.entity, f"recognizers.{rid}.entity")
            if isinstance(spec, PresidioSpec):
                for e in spec.entity_map.values():
                    need(e, f"recognizers.{rid}.entity_map")
            if isinstance(spec, RegistrySpec):
                for k in spec.kinds:
                    if self.registry.kind_entities and k not in self.registry.kind_entities:
                        errs.append(f"recognizers.{rid}: kind {k!r} missing from registry.kind_entities")
        for k, es in self.registry.kind_entities.items():
            for e in es:
                need(e, f"registry.kind_entities.{k}")
        for prof, names in self.normalization_profiles.items():
            for n in names:
                if n not in self.normalizers:
                    errs.append(f"normalization_profiles.{prof}: unknown normalizer {n!r}")
        for e, op in self.redaction.per_entity.items():
            need(e, "redaction.per_entity")
        for c in self.failure.startup_self_test.cases:
            for e in c.entities:
                need(e, "failure.startup_self_test")
        for rid in self.outbound_guard.recognizers:
            if rid not in self.recognizers:
                errs.append(f"outbound_guard.recognizers: unknown recognizer {rid!r}")
        for r in self.contextual_type_rules:
            for e in r.from_types + [r.to]:
                need(e, f"contextual_type_rules.{r.id}")
        for c in self.monitoring.canaries.cases:
            for e in c.entities:
                need(e, f"monitoring.canaries.{c.id}")
            if c.normalization_profile not in self.normalization_profiles:
                errs.append(f"monitoring.canaries.{c.id}: unknown normalization profile {c.normalization_profile!r}")
        if errs:
            raise ValueError("; ".join(errs))
        return self
