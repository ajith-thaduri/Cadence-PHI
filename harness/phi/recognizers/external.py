"""Presidio predefined recognizers (comparison / defence-in-depth), labels mapped to the taxonomy."""
from __future__ import annotations

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit


class PresidioBuiltinRecognizer(PHIRecognizer):
    source = EvidenceSource.external

    def __init__(self, recognizer_id: str, presidio_class: str, entity_map: dict[str, str], language: str = "en",
                 version: str = "presidio") -> None:
        super().__init__(recognizer_id, sorted(set(entity_map.values())), language, version)
        import presidio_analyzer.predefined_recognizers as P
        self.inner = getattr(P, presidio_class)(supported_language=language)
        self.entity_map = entity_map

    def hits(self, text: str) -> list[RawHit]:
        return [RawHit(self.entity_map[r.entity_type], r.start, r.end, float(r.score), ScoreType.regex_match,
                       rule_id=getattr(r.analysis_explanation, "pattern_name", None))
                for r in self.inner.analyze(text, list(self.entity_map), None) or [] if r.entity_type in self.entity_map]
