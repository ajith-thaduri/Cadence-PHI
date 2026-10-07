"""Cadence policy: entity enablement, thresholds, precedence, decisions, confidence class.

Presidio proposes; policy decides. Nothing here is a probability.

  annotate()  for every evidence item: is it allowed by config + request allow-lists? which
              threshold applies (and which config key supplied it)? accepted or rejected (why)?
  decide()    reconcile accepted evidence into groups, then for each group choose the entity
              type (precedence by score_type, then raw score), the decision (REDACT / REVIEW /
              KEEP), the confidence class and a human-readable reason.

confidence_class
  high    any registry_exact / checksum_valid / registry_normalized evidence, or >= 2 independent
          sources (e.g. rule + model, registry + model)
  medium  a single deterministic / contextual source, or a model score >= threshold + medium_margin
  low     a single model just above threshold
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

from harness.phi.config import HarnessConfig
from harness.phi.contracts import (ConfidenceClass, DecisionValue, Detection, DetectionEvidence, DetectionRequest,
                                   EvidenceSource, RejectedEvidenceRef, RejectionReason, stable_id)
from harness.phi.reconciliation import VERSION as RECON_VERSION
from harness.phi.reconciliation import OverlapGroup, ReconParams, reconcile


def allowed(ev: DetectionEvidence, cfg: HarnessConfig, req: Optional[DetectionRequest],
            normalizers: Optional[list[str]] = None) -> Optional[RejectionReason]:
    """Why config / request excludes this evidence (None = allowed)."""
    if not (cfg.raw["entities"].get(ev.entity_type) or {}).get("enabled", True):
        return RejectionReason.entity_disabled
    if req is not None and req.enabled_entities is not None and ev.entity_type not in set(cfg.expand(req.enabled_entities)):
        return RejectionReason.entity_disabled
    if ev.source == EvidenceSource.model:
        key = ev.recognizer_id.removeprefix("model_")
        if key not in cfg.enabled_models or (req is not None and req.enabled_models is not None and key not in req.enabled_models):
            return RejectionReason.model_disabled
    else:
        if not cfg.recognizer_enabled(ev.recognizer_id) or (
                req is not None and req.enabled_recognizers is not None and ev.recognizer_id not in req.enabled_recognizers):
            return RejectionReason.recognizer_disabled
    if ev.recognizer_id in cfg.shadow_ids:
        return RejectionReason.shadow_mode
    for t in ev.metadata.get("transformations") or []:
        nid = t.rsplit("_v", 1)[0]
        if normalizers is not None and nid not in normalizers:
            return RejectionReason.normalizer_disabled
    if ev.metadata.get("approximate_mapping"):
        return RejectionReason.approximate_mapping
    return None


@lru_cache(maxsize=16)
def _non_phi_compiled(spec_json: str):
    import json
    spec = json.loads(spec_json)
    phrases = []
    for p in spec.get("non_phi_phrases") or []:
        terms = "|".join(sorted((re.escape(t) for t in p["terms"]), key=len, reverse=True))
        tail = rf"(?:[\s-](?!(?:{'|'.join(_STOP)})\b)[A-Z0-9][\w-]*){{0,{int(p.get('max_tail_tokens', 2))}}}"
        rx = re.compile(rf"(?P<year>\b(?:19|20)\d{{2}}\s)?\b(?:{terms})\b(?P<tail>{tail})")
        cues = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in p.get("cues", [])) + r")\b", re.I) if p.get("cues") else None
        phrases.append((rx, bool(p.get("require_year_or_tail", True)), cues, int(p.get("window", 40))))
    pats = {e: [re.compile(x) for x in xs] for e, xs in (spec.get("non_phi_patterns") or {}).items()}
    return phrases, pats


_STOP = ("And", "Or", "The", "In", "At", "On", "Of", "For", "With", "By", "From", "To")


def _non_phi_spans(text: str, cfg: HarnessConfig) -> list[tuple[int, int]]:
    """Character ranges of configured non-PHI phrases (e.g. "2021 Toyota Camry" near a vehicle cue)."""
    import json
    spec = {k: cfg.raw.get(k) for k in ("non_phi_phrases", "non_phi_patterns")}
    if not spec["non_phi_phrases"]:
        return []
    phrases, _ = _non_phi_compiled(json.dumps(spec, sort_keys=True))
    out = []
    for rx, need, cues, window in phrases:
        for m in rx.finditer(text):
            if need and not (m.group("year") or m.group("tail").strip()):
                continue
            if cues is not None and not cues.search(text[max(0, m.start() - window):m.end() + window]):
                continue
            out.append(m.span())
    return out


def annotate(evidence: list[DetectionEvidence], cfg: HarnessConfig, req: Optional[DetectionRequest],
             normalizers: Optional[list[str]] = None) -> list[DetectionEvidence]:
    import json
    allow = {a.strip().lower() for a in (cfg.raw.get("allow_list") or []) if a.strip()}
    nonphi = _non_phi_spans(req.text, cfg) if req is not None else []
    _, pats = _non_phi_compiled(json.dumps({k: cfg.raw.get(k) for k in ("non_phi_phrases", "non_phi_patterns")}, sort_keys=True))
    mods = cfg.raw.get("non_phi_pattern_modalities") or []
    pat_applies = req is not None and (not mods or str(getattr(req.modality, "value", req.modality)) in mods)
    out = []
    for ev in evidence:
        why = allowed(ev, cfg, req, normalizers)
        if why is None and allow and req is not None and \
                req.text[ev.original_start:ev.original_end].strip().lower() in allow:
            why = RejectionReason.allow_listed
        if why is None and req is not None and ev.entity_type != "VEHICLE_ID" and \
                any(s <= ev.original_start and ev.original_end <= e for s, e in nonphi):
            why = RejectionReason.non_phi                  # entirely inside "2021 Toyota Camry" etc.
        if why is None and req is not None and pat_applies and any(rx.fullmatch(req.text[ev.original_start:ev.original_end].strip())
                                                                   for rx in pats.get(ev.entity_type, [])):
            why = RejectionReason.non_phi                  # e.g. a year alone is not a DATE
        thr, src = cfg.threshold_for(ev.entity_type, ev.recognizer_id, ev.model_id and ev.recognizer_id.removeprefix("model_"),
                                     ev.score_type if isinstance(ev.score_type, str) else ev.score_type.value,
                                     getattr(req, "modality", None) if req is not None else None)
        if why is None and ev.raw_score < thr:
            why = RejectionReason.below_threshold
        upd = {"threshold_used": thr, "threshold_source": src, "accepted_as_candidate": why is None,
               "rejection_reason": None if why is None else why}
        if why == RejectionReason.shadow_mode:            # what the shadow WOULD have contributed (monitoring only)
            upd["metadata"] = {**ev.metadata, "shadow_would_accept": ev.raw_score >= thr}
        out.append(ev.model_copy(update=upd))
    return out


def _covers_content(text: str, outer: DetectionEvidence, inner: DetectionEvidence) -> bool:
    """outer covers every alphanumeric character of inner (edge punctuation like "#7788" or "(X)" is not content)."""
    return all(outer.original_start <= i < outer.original_end
               for i in range(inner.original_start, inner.original_end) if text[i].isalnum())


def _st(ev: DetectionEvidence) -> str:
    return ev.score_type if isinstance(ev.score_type, str) else ev.score_type.value


def _reason(acc: list[DetectionEvidence], sel: DetectionEvidence) -> str:
    srcs = sorted({("ML" if (ev.source == "model" or ev.source == EvidenceSource.model) else
                    (ev.source if isinstance(ev.source, str) else ev.source.value)) for ev in acc})
    recs = sorted({ev.recognizer_id for ev in acc})
    if len(srcs) > 1:
        r = f"{_st(sel)} + " + " + ".join(s for s in srcs) + " agreement"
    elif len(recs) > 1:
        r = f"{len(recs)} {srcs[0]} recognizers agree"
    else:
        r = f"single source: {_st(sel)} ({sel.recognizer_id})"
    types = sorted({ev.entity_type for ev in acc})
    if len(types) > 1:
        r += f"; type conflict {'/'.join(types)} -> {sel.entity_type} by precedence ({_st(sel)})"
    if any(ev.metadata.get("transformations") for ev in acc):
        r += "; via normalized view"
    return r


def _confidence(acc: list[DetectionEvidence], sel: DetectionEvidence, pol: dict[str, Any]) -> ConfidenceClass:
    conf = pol.get("confidence") or {}
    if any(_st(ev) in conf.get("high_score_types", []) for ev in acc):
        return ConfidenceClass.high
    srcs = {str(ev.source) for ev in acc}
    if len(srcs) >= 2:
        return ConfidenceClass.high
    if _st(sel) != "model_confidence":
        return ConfidenceClass.medium
    if sel.threshold_used is not None and sel.raw_score >= sel.threshold_used + float(conf.get("medium_margin", 0.2)):
        return ConfidenceClass.medium
    return ConfidenceClass.low


def _ref(ev: DetectionEvidence) -> RejectedEvidenceRef:
    return RejectedEvidenceRef(evidence_id=ev.evidence_id, recognizer_id=ev.recognizer_id, entity_type=ev.entity_type,
                               raw_score=ev.raw_score, score_type=ev.score_type, threshold_used=ev.threshold_used,
                               reason=ev.rejection_reason)


def decide(request_id: str, text: str, evidence: list[DetectionEvidence], cfg: HarnessConfig, policy_profile: str,
           include_text: bool = False) -> tuple[list[Detection], list[OverlapGroup]]:
    """`evidence` must already be annotated. Returns detections sorted by offset."""
    pol = cfg.policy(policy_profile)
    rank = {t: i for i, t in enumerate(cfg.raw["precedence"])}
    band = float(pol.get("review_band", 0.0) or 0.0)
    pv = cfg.raw.get("engine", {}).get("policy_version", "cadence-policy")
    operator = pol.get("operator", "TOKENIZE")
    groups = reconcile(text, evidence, ReconParams.from_config(cfg.raw.get("reconciliation")))
    ctx_rules = _context_rules(cfg)
    dets: list[Detection] = []
    acc_groups = [x for x in groups if x.accepted]
    for g in groups:
        prev_end = max((x.end for x in acc_groups if x.end <= g.start), default=0)
        next_start = min((x.start for x in acc_groups if x.start >= g.end), default=len(text))
        if g.accepted:
            acc = g.accepted
            # precedence by score type; within a score type, evidence covering the WHOLE group describes it better
            # than a fragment (an IP address "10.20.30.40" over a date-shaped "10.20.30" inside it)
            sel = min(acc, key=lambda ev: (rank.get(_st(ev), len(rank)),
                                           0 if (ev.original_start <= g.start and ev.original_end >= g.end) else 1,
                                           -ev.raw_score, -(ev.original_end - ev.original_start), ev.evidence_id))
            # a subtype refines the selected type only if its evidence covers the whole selected span
            # (a contact's surname alone must not relabel the member's full name)
            refined = [ev for ev in acc if ev.entity_type in (cfg.raw.get("type_refinements") or {}).get(sel.entity_type, [])
                       and _covers_content(text, ev, sel)
                       # an explicit field label ("insurer <name>") outranks a subtype guessed by the model alone
                       and not (sel.recognizer_id.startswith("labelled_") and _st(ev) == "model_confidence")]
            if refined:   # a more specific subtype of the selected type (DATE -> DOB)
                sel = min(refined, key=lambda ev: (rank.get(_st(ev), len(rank)), -ev.raw_score, ev.evidence_id))
            decision, reason = DecisionValue.REDACT, _reason(acc, sel)
            ctx = _context_type(text, g.start, g.end, sel.entity_type, ctx_rules, bounds=(prev_end, next_start),
                                model_only=not any(ev.entity_type == sel.entity_type and _st(ev) != "model_confidence" for ev in acc))
            if ctx is not None:
                sel = sel.model_copy(update={"entity_type": ctx[0]})
                reason += f"; contextual type {ctx[0]} (cue rule {ctx[1]})"
            conf = _confidence(acc, sel, pol)
            contributing = acc
        else:
            live = [ev for ev in g.rejected if ev.rejection_reason == RejectionReason.below_threshold]
            if not live:
                continue      # only disabled evidence: nothing this configuration runs saw the span
            sel = max(live, key=lambda ev: (ev.raw_score - (ev.threshold_used or 0), ev.evidence_id))
            near = band > 0 and _st(sel) == "model_confidence" and sel.raw_score >= (sel.threshold_used or 0) - band
            decision = DecisionValue.REVIEW if near else DecisionValue.KEEP
            reason = (f"model evidence within review band ({sel.raw_score:.3f} vs threshold {sel.threshold_used})" if near
                      else "all evidence below threshold")
            conf = ConfidenceClass.low
            contributing = []
        redact = decision == DecisionValue.REDACT or (decision == DecisionValue.REVIEW and pol.get("redact_review", True))
        dets.append(Detection(
            detection_id=stable_id("det", request_id, g.start, g.end, sel.entity_type, decision),
            entity_type=sel.entity_type, original_start=g.start, original_end=g.end, raw_text_available=True,
            text=text[g.start:g.end] if include_text else None,
            redaction_action=operator if redact else None,
            decision_score=round(sel.raw_score, 6), decision_score_type="policy_aggregate",
            threshold=sel.threshold_used, decision=decision, confidence_class=conf, decision_reason=reason,
            contributing_evidence_ids=[ev.evidence_id for ev in contributing],
            contributing_recognizers=sorted({ev.recognizer_id for ev in contributing}),
            rejected_evidence=[_ref(ev) for ev in g.rejected],
            policy_version=pv, reconciliation_version=RECON_VERSION))
    return _merge_fragments(text, dets, cfg), groups


# ---------------------------------------------------------------------------- contextual typing (label only)
@lru_cache(maxsize=32)
def _compile_rules(rules_json: str) -> list[tuple[str, frozenset, str, re.Pattern, Optional[re.Pattern], int]]:
    import json
    out = []
    for r in json.loads(rules_json):
        cues = "|".join(sorted((re.escape(c) for c in r["cues"]), key=len, reverse=True))
        bridge = r.get("bridge", r"(?:\s*(?:\b(?:is|was|named|called|her|his|my|their|our|the|patient's|member's)\b|[:,\-]))*\s*")
        rx = re.compile(rf"\b(?:{cues})\b{bridge}$", re.I)
        unless = re.compile(r["unless_preceded_by"] + r"\s*$") if r.get("unless_preceded_by") else None
        if r.get("mode") == "requires":
            rx = re.compile(rf"\b(?:{cues})(?![A-Za-z])", re.I)
        out.append((r["id"], frozenset(r["from"]), r["to"], rx, unless, int(r.get("window", 40)), r.get("mode", "cue")))
    return out


def _context_rules(cfg: HarnessConfig):
    import json
    rules = cfg.raw.get("contextual_type_rules") or []
    return _compile_rules(json.dumps(rules, sort_keys=True)) if rules else []


def _context_type(text: str, start: int, end: int, etype: str, rules, model_only: bool,
                  bounds: tuple[int, int] = (0, 10 ** 9)) -> Optional[tuple[str, str]]:
    """cue rules: a relation / role cue directly before the span ("emergency contact was", "her daughter",
    "employer,") refines the type; only connector words may sit between cue and span.
    requires rules: a type proposed by the model alone, with no supporting cue nearby, falls back to `to`
    (a model-only PROVIDER_NAME with no title / clinical role nearby is treated as the patient's NAME - the
    more protective type). The extent never changes."""
    for rid, frm, to, rx, unless, window, mode in rules:
        if etype not in frm or etype == to:
            continue
        # a cue belongs to the span only if no other detected span sits between them ("Dr. A. Patel, claims
        # examiner T. Greene": the "Dr." belongs to Patel)
        left = text[max(0, start - window, bounds[0]):start]
        right = text[end:min(end + window, bounds[1])]
        if mode == "requires":
            if model_only and not (rx.search(left) or rx.search(right)):
                return to, rid
            continue
        if unless is not None and unless.search(left):
            continue
        if rx.search(left):
            return to, rid
    return None


# ---------------------------------------------------------------------------- fragment merge
def _merge_fragments(text: str, dets: list[Detection], cfg: HarnessConfig) -> list[Detection]:
    """Adjacent detections of the same type separated only by intra-token punctuation ("POL" "/" "99281" "-" "A")
    are one value: merged into one detection so it gets one token and one type."""
    joiners = (cfg.raw.get("reconciliation") or {}).get("fragment_joiners", "")
    if not joiners or len(dets) < 2:
        return dets
    out = [dets[0]]
    for d in dets[1:]:
        prev = out[-1]
        gap = text[prev.original_end:d.original_start]
        if (prev.decision == d.decision == DecisionValue.REDACT and prev.entity_type == d.entity_type
                and 0 < len(gap) <= 2 and all(c in joiners for c in gap)):
            out[-1] = prev.model_copy(update={
                "detection_id": stable_id("det", prev.detection_id, d.detection_id), "original_end": d.original_end,
                "decision_score": max(prev.decision_score, d.decision_score),
                "contributing_evidence_ids": prev.contributing_evidence_ids + d.contributing_evidence_ids,
                "contributing_recognizers": sorted(set(prev.contributing_recognizers) | set(d.contributing_recognizers)),
                "rejected_evidence": prev.rejected_evidence + d.rejected_evidence,
                "decision_reason": prev.decision_reason + "; merged fragment",
                "text": text[prev.original_start:d.original_end] if prev.text is not None else None})
        else:
            out.append(d)
    return out
