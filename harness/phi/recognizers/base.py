"""The single recognizer contract.

  recognize(ctx, view) -> list[DetectionEvidence]      (ORIGINAL-text offsets)

Every recognizer is also a Presidio EntityRecognizer, so Presidio's RecognizerRegistry /
AnalyzerEngine orchestrate selection (language, entities, ad-hoc per-request recognizers) and
invocation. Internally a recognizer produces RawHits on whatever view it reads; the base class
carries them through Presidio's RecognizerResult (metadata keeps every field) and projects
them to original offsets. Recognizers only PROPOSE evidence: they never threshold, reconcile,
redact or modify text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from presidio_analyzer import AnalysisExplanation, EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from harness.phi.contracts import DetectionEvidence, EvidenceSource, MappingQuality, ScoreType, TextView, stable_id
from harness.taxonomy import CANONICAL_LABELS

NUMBER_TRANSFORMS = ("spoken_digits", "spoken_dates", "spoken_address")


@dataclass
class RawHit:
    """A recognizer's hit on the text it was given (that text's offsets)."""
    entity_type: str
    start: int
    end: int
    raw_score: float
    score_type: ScoreType
    rule_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecognitionContext:
    request_id: str
    language: str
    registry: Optional[dict[str, Any]] = None          # scoped, resolved by RegistryStore
    registry_versions: dict[str, str] = field(default_factory=dict)


class PHIRecognizer(EntityRecognizer):
    source: EvidenceSource = EvidenceSource.rule
    version: str = "1"
    model_id: Optional[str] = None
    model_revision: Optional[str] = None

    def __init__(self, recognizer_id: str, supported_entities: Optional[List[str]] = None, language: str = "en",
                 version: str = "1") -> None:
        super().__init__(supported_entities=supported_entities or list(CANONICAL_LABELS), name=recognizer_id,
                         supported_language=language, version=version)
        self.recognizer_id = recognizer_id
        self.version = version

    def load(self) -> None:   # heavy resources are shared and loaded by the engine
        pass

    # ---- implemented by subclasses -----------------------------------------------------------
    def hits(self, text: str) -> list[RawHit]:
        raise NotImplementedError

    # ---- Presidio interface --------------------------------------------------------------------
    def analyze(self, text: str, entities: List[str], nlp_artifacts: Optional[NlpArtifacts] = None) -> List[RecognizerResult]:
        want = set(entities or [])
        out = []
        for h in self.hits(text):
            if (want and h.entity_type not in want) or h.end <= h.start:
                continue
            expl = AnalysisExplanation(recognizer=self.recognizer_id, original_score=h.raw_score,
                                       pattern_name=h.rule_id, textual_explanation=f"{self.source.value}/{h.score_type.value}")
            meta = {RecognizerResult.RECOGNIZER_NAME_KEY: self.recognizer_id,
                    RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id, "phi_hit": h, "phi_recognizer": self}
            # Presidio requires [0, 1]; every recognizer here emits raw scores in [0, 1] already.
            out.append(RecognizerResult(h.entity_type, h.start, h.end, float(min(1.0, max(0.0, h.raw_score))), expl, meta))
        return out

    # ---- contract -------------------------------------------------------------------------------
    def recognize(self, ctx: RecognitionContext, view: TextView, entities: Optional[list[str]] = None) -> list[DetectionEvidence]:
        return [evidence_from_result(r, ctx, view) for r in self.analyze(view.text, entities or [], None)]


def evidence_from_result(r: RecognizerResult, ctx: RecognitionContext, view: TextView) -> Optional[DetectionEvidence]:
    """Presidio RecognizerResult (from one of our recognizers) -> DetectionEvidence in ORIGINAL offsets."""
    md = r.recognition_metadata or {}
    h: RawHit = md["phi_hit"]
    rec: PHIRecognizer = md["phi_recognizer"]
    s, e, used = view.to_original(r.start, r.end)
    if e <= s:
        return None
    score_type = h.score_type
    if used and score_type == ScoreType.regex_match and any(u.startswith(NUMBER_TRANSFORMS) for u in used):
        score_type = ScoreType.spoken_number_match
    meta = dict(h.metadata)
    if used:
        meta["transformations"] = used
    if view.mapping_quality == MappingQuality.approximate:
        meta["approximate_mapping"] = True
    if getattr(r.analysis_explanation, "score_context_improvement", 0):
        meta["presidio_context_improvement"] = r.analysis_explanation.score_context_improvement
    return DetectionEvidence(
        evidence_id=stable_id("ev", ctx.request_id, rec.recognizer_id, view.view_id, s, e, h.entity_type, h.rule_id),
        entity_type=h.entity_type, original_start=s, original_end=e, raw_score=float(h.raw_score), score_type=score_type,
        recognizer_id=rec.recognizer_id, recognizer_version=rec.version, model_id=rec.model_id,
        model_revision=rec.model_revision, rule_id=h.rule_id, source=rec.source, text_view_id=view.view_id, metadata=meta)
