"""Outbound guard: an independent last-line re-scan of SANITIZED text before it leaves the trust boundary
(e.g. before a prompt is sent to an LLM). Plan §11 / NIST SP 800-53 SC-7 (boundary protection).

Independence: the guard runs only deterministic evidence - rule families, declarative patterns,
conversational rules and the request's scoped registry - never the contextual model, so a model blind spot
cannot hide its own leak. It is a separate PHIEngine instance built from the same configuration with every
model disabled, and it ignores the tokens the primary engine issued (exact token strings from the vault).

Decision: any hit on an entity whose catalog `outbound_guard` is `block` -> BLOCK (when
outbound_guard.action is block); `flag` entities are recorded only. The result carries entity types, offsets
and recognizer ids - never the matched text - so it can be logged and counted (guard_hits).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from harness.phi import telemetry
from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness.phi.contracts import DecisionValue, DetectionOptions, DetectionRequest, Mode

log = logging.getLogger("harness.phi.outbound_guard")


@dataclass
class GuardHit:
    entity_type: str
    start: int
    end: int
    recognizers: list[str]
    blocking: bool


@dataclass
class GuardResult:
    allowed: bool
    hits: list[GuardHit] = field(default_factory=list)
    status: str = "ok"                      # ok | blocked_guard_error (the guard itself failed -> fail closed)

    @property
    def blocking_hits(self) -> list[GuardHit]:
        return [h for h in self.hits if h.blocking]


class OutboundGuard:
    def __init__(self, cfg: HarnessConfig, registry_store=None) -> None:
        from harness.phi.engine import PHIEngine
        g = cfg.raw.get("outbound_guard") or {}
        self.enabled = bool(g.get("enabled", True))
        self.action = g.get("action", "block")
        gcfg = cfg.copy()                                   # the primary config is never mutated
        gcfg.only_models([])
        only = g.get("recognizers") or []
        if only:
            gcfg.set_recognizers([r for r in gcfg.raw["recognizers"] if r not in only], False)
        self.catalog = Catalog(cfg)
        self.block_entities = set(self.catalog.outbound_block())
        self._engine = PHIEngine(gcfg, registry_store=registry_store, self_test=False)

    def check(self, sanitized_text: str, issued_tokens: Optional[list[str]] = None, request_id: str = "guard",
              registry_context=None, language: str = "en") -> GuardResult:
        if not self.enabled:
            return GuardResult(allowed=True)
        token_spans: list[tuple[int, int]] = []
        if issued_tokens:
            rx = re.compile("|".join(re.escape(t) for t in sorted(set(issued_tokens), key=len, reverse=True)))
            token_spans = [(m.start(), m.end()) for m in rx.finditer(sanitized_text)]
        try:
            resp = self._engine.analyze(DetectionRequest(request_id=f"{request_id}_guard", text=sanitized_text, language=language,
                                                         registry_context=registry_context,
                                                         options=DetectionOptions(mode=Mode.DETECT)))
        except Exception:  # noqa: BLE001 - a guard that cannot run must not wave the payload through
            telemetry.count("outbound_guard_error")
            return GuardResult(allowed=False, status="blocked_guard_error")
        if resp.status != "ok":
            telemetry.count("outbound_guard_error")
            return GuardResult(allowed=False, status="blocked_guard_error")
        hits = []
        for d in resp.detections:
            if d.decision not in (DecisionValue.REDACT, DecisionValue.REVIEW):
                continue
            if any(s <= d.original_start and d.original_end <= e for s, e in token_spans):
                continue                                           # the engine's own token, not a leak
            hits.append(GuardHit(d.entity_type, d.original_start, d.original_end, list(d.contributing_recognizers),
                                 d.entity_type in self.block_entities))
        allowed = not (self.action == "block" and any(h.blocking for h in hits))
        if hits:
            telemetry.count("outbound_guard_hit", len(hits))
            telemetry.event(log, "outbound_guard", request_id=request_id, guard_hits=len(hits),
                            action="block" if not allowed else "flag",
                            entities_by_type={t: sum(h.entity_type == t for h in hits) for t in {h.entity_type for h in hits}})
        if not allowed:
            telemetry.count("outbound_guard_block")
        return GuardResult(allowed=allowed, hits=hits)
