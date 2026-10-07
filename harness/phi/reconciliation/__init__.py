"""Reconciliation: group overlapping evidence into non-overlapping spans (ORIGINAL offsets).

  - only ACCEPTED evidence defines redaction extent; each accepted span is first expanded to
    whole-word boundaries ("Thom|pson" -> "Thompson")
  - accepted spans that overlap form one group; the group extent is their union (recall first)
  - bridged groups are SPLIT (reconciliation.split_bridged_groups): when a group contains two or more
    disjoint clusters of deterministic evidence with different entity types, joined only through a model
    span ("AZ 85201" = STATE + ZIP, "12 Elm St, Mesa" = ADDRESS + CITY), each cluster becomes
    its own group; text between clusters stays redacted (its own group, typed by the model) unless it is
    only connector words / punctuation ("by", ", "), which are not PHI
  - field labels at a group edge ("MAC 3C:52:..", "IP 10.0.0.1") are trimmed; a group that is only a label
    is dropped - a label names a field, it is not the value (reconciliation.field_labels, case-sensitive)
  - rejected evidence that overlaps a group is attached to it (for the evidence chain);
    rejected evidence overlapping no group forms KEEP / REVIEW groups of its own
Type selection and the final decision are POLICY (harness/phi/policy), not done here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from harness.phi.contracts import DetectionEvidence

VERSION = "overlap-union-wordexpand-split-labels 2.0"


@dataclass(frozen=True)
class ReconParams:
    split_bridged_groups: bool = False
    connector_words: frozenset[str] = frozenset()
    field_labels: frozenset[str] = frozenset()
    abbreviation_joiners: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, spec: Optional[dict[str, Any]]) -> "ReconParams":
        s = spec or {}
        return cls(bool(s.get("split_bridged_groups", False)),
                   frozenset(w.lower() for w in s.get("connector_words", [])),
                   frozenset(s.get("field_labels", [])),
                   frozenset(s.get("abbreviation_joiners", [])))


def expand_to_words(text: str, s: int, e: int) -> tuple[int, int]:
    while s > 0 and text[s - 1].isalnum() and text[s].isalnum():
        s -= 1
    while e < len(text) and text[e].isalnum() and text[e - 1].isalnum():
        e += 1
    return s, e


@dataclass
class OverlapGroup:
    start: int
    end: int
    accepted: list[DetectionEvidence] = field(default_factory=list)
    rejected: list[DetectionEvidence] = field(default_factory=list)


def _merge(items: list[tuple[int, int, DetectionEvidence]]) -> list[OverlapGroup]:
    items.sort(key=lambda x: (x[0], -x[1], x[2].evidence_id))
    groups: list[OverlapGroup] = []
    for s, e, ev in items:
        if groups and s < groups[-1].end:
            groups[-1].end = max(groups[-1].end, e)
            groups[-1].accepted.append(ev)
        else:
            groups.append(OverlapGroup(s, e, [ev]))
    return groups


def is_model(ev: DetectionEvidence) -> bool:
    st = ev.score_type if isinstance(ev.score_type, str) else ev.score_type.value
    return st == "model_confidence"


_WORD = re.compile(r"[\w'’]+")


def _only_connectors(seg: str, p: ReconParams) -> bool:
    words = _WORD.findall(seg)
    return all(w.lower() in p.connector_words for w in words)


def _split_bridged(text: str, g: OverlapGroup, p: ReconParams) -> list[OverlapGroup]:
    det = [ev for ev in g.accepted if not is_model(ev)]
    if len(det) < 2:
        return [g]
    clusters = _merge([(*expand_to_words(text, ev.original_start, ev.original_end), ev) for ev in det])
    if len(clusters) < 2:
        return [g]
    type_sets = [{ev.entity_type for ev in c.accepted} for c in clusters]
    if set.intersection(*type_sets):          # every cluster shares a type: one entity, keep the union
        return [g]
    models = [ev for ev in g.accepted if is_model(ev)]
    out: list[OverlapGroup] = []
    for c in clusters:
        c.accepted += [m for m in models if m.original_start < c.end and c.start < m.original_end]
        out.append(c)
    # residual: characters of the union not inside any cluster
    cuts = [(g.start, g.start)] + [(c.start, c.end) for c in clusters] + [(g.end, g.end)]
    for (_, a), (b, _) in zip(cuts, cuts[1:]):
        if b <= a:
            continue
        seg = text[a:b]
        s, e = a + (len(seg) - len(seg.lstrip(" \t\n,;:.-/"))), b - (len(seg) - len(seg.rstrip(" \t\n,;:.-/")))
        if e <= s or _only_connectors(text[s:e], p):
            continue
        cover = [m for m in models if m.original_start < e and s < m.original_end]
        if cover:
            out.append(OverlapGroup(s, e, cover))
    return sorted(out, key=lambda x: (x.start, x.end))


def _trim_labels(text: str, g: OverlapGroup, p: ReconParams) -> Optional[OverlapGroup]:
    def is_entity(ls: int, le: int) -> bool:
        # a deterministic recognizer typed exactly this token (Idaho's "ID" in "Boise, ID 83702"): not a field label
        return any(not is_model(ev) and ev.original_start >= ls and ev.original_end <= le for ev in g.accepted)
    s, e = g.start, g.end
    while True:
        m = re.match(r"([A-Za-z]+)([\s:#=]+)", text[s:e])
        if not (m and m.group(1) in p.field_labels) or is_entity(s, s + len(m.group(1))):
            break
        s += m.end()
    while True:
        m = re.search(r"([\s:#=]+)([A-Za-z]+)$", text[s:e])
        if not (m and m.group(2) in p.field_labels) or is_entity(s + m.start(2), e):
            break
        e = s + m.start()
    if not any(ch.isalnum() for ch in text[s:e]) or (text[s:e] in p.field_labels and not is_entity(s, e)):
        return None
    g.start, g.end = s, e
    return g


def _join_initials(text: str, groups: list[OverlapGroup], p: ReconParams) -> list[OverlapGroup]:
    """"Kevin L" + ". " + "Harper", "St" + ". " + "Agnes Hospital": one name split at an initial / abbreviation period.
    Joined BEFORE typing when both groups share an entity type, so the name is typed as one unit."""
    out: list[OverlapGroup] = []
    for g in groups:
        prev = out[-1] if out else None
        if prev is not None and text[prev.end:g.start] == ". ":
            tok = re.search(r"([A-Za-z]+)$", text[prev.start:prev.end])
            shared = {e.entity_type for e in prev.accepted} & {e.entity_type for e in g.accepted}
            if tok and shared and (re.fullmatch(r"[A-Z]", tok.group(1)) or tok.group(1) in p.abbreviation_joiners):
                prev.end, prev.accepted = g.end, prev.accepted + g.accepted
                continue
        out.append(g)
    return out


def reconcile(text: str, evidence: list[DetectionEvidence], params: Optional[ReconParams] = None) -> list[OverlapGroup]:
    p = params or ReconParams()
    acc = [ev for ev in evidence if ev.accepted_as_candidate]
    rej = [ev for ev in evidence if not ev.accepted_as_candidate]
    groups = _merge([(*expand_to_words(text, ev.original_start, ev.original_end), ev) for ev in acc])
    if p.split_bridged_groups:
        groups = [x for g in groups for x in _split_bridged(text, g, p)]
    if p.field_labels:
        groups = [x for x in (_trim_labels(text, g, p) for g in groups) if x is not None]
    if p.abbreviation_joiners:
        groups = _join_initials(text, groups, p)
    loose = []
    for ev in rej:
        g = next((g for g in groups if ev.original_start < g.end and g.start < ev.original_end), None)
        if g is not None:
            g.rejected.append(ev)
        else:
            loose.append(ev)
    keep_groups = _merge([(ev.original_start, ev.original_end, ev) for ev in loose])
    for g in keep_groups:          # these hold only rejected evidence
        g.rejected, g.accepted = g.accepted, []
    if p.field_labels:
        keep_groups = [x for x in (_trim_labels(text, g, p) for g in keep_groups) if x is not None]
    return sorted(groups + keep_groups, key=lambda g: (g.start, g.end))
