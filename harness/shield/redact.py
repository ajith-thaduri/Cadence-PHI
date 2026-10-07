"""Consistent tokenisation (redaction) and restoration.

Each distinct (label, normalised surface) gets a stable token such as [NAME_1], reused for
repeated mentions within the document - the Bedrock prompt keeps referential structure
while the identifier never leaves the VPC. `restore` inverts the mapping exactly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.schema import Prediction


@dataclass
class Redaction:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)          # token -> original surface
    redacted_mask: list[bool] = field(default_factory=list)        # per ORIGINAL character
    tokens: list[tuple[int, int, str, str]] = field(default_factory=list)  # (orig_start, orig_end, token, surface)


def _key(label: str, surface: str) -> tuple[str, str]:
    return label, re.sub(r"\W+", "", surface.lower())


def redact(text: str, spans: list[Prediction]) -> Redaction:
    spans = sorted(spans, key=lambda p: p.start)
    ids: dict[tuple[str, str], str] = {}
    counters: dict[str, int] = {}
    parts, mapping, toks = [], {}, []
    mask = [False] * len(text)
    pos = 0
    for p in spans:
        if p.start < pos:
            continue   # reconcile() guarantees non-overlap; defensive
        parts.append(text[pos:p.start])
        k = _key(p.canonical_label, p.text)
        if k not in ids:
            counters[p.canonical_label] = counters.get(p.canonical_label, 0) + 1
            ids[k] = f"[{p.canonical_label}_{counters[p.canonical_label]}]"
        tok = ids[k]
        mapping.setdefault(tok, text[p.start:p.end])
        parts.append(tok)
        toks.append((p.start, p.end, tok, text[p.start:p.end]))
        for i in range(p.start, p.end):
            mask[i] = True
        pos = p.end
    parts.append(text[pos:])
    return Redaction(text="".join(parts), mapping=mapping, redacted_mask=mask, tokens=toks)


def restore(red: Redaction, original_surfaces: bool = True) -> str:
    """Rebuild the original text from redacted output + token spans (exact for this document)."""
    out, pos = [], 0
    for _, _, tok, surface in red.tokens:
        i = red.text.index(tok, pos)
        out.append(red.text[pos:i])
        out.append(surface if original_surfaces else tok)
        pos = i + len(tok)
    out.append(red.text[pos:])
    return "".join(out)
