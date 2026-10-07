"""Entity reconciliation: union of all detector outputs, merged into non-overlapping spans.

Policy (recall-first):
  - any character claimed by any detector is redacted (union)
  - overlapping or touching spans merge; the merged span keeps the label of the
    highest-priority contributor (structured identifiers > names > locations > dates > other)
  - spans are expanded to whole-word boundaries ("Thomp|son" -> "Thompson")
"""
from __future__ import annotations

from harness.schema import Prediction

PRIORITY = ["MRN", "MEMBER_ID", "NPI", "ACCOUNT_ID", "PHONE", "EMAIL", "URL", "OTHER_UNIQUE_ID", "DOB",
            "NAME", "PROVIDER_NAME", "RELATIVE_NAME", "CARE_TEAM_MEMBER", "FACILITY_NAME", "ADDRESS", "ZIP",
            "CITY", "STATE", "DATE", "EMPLOYER", "ORGANIZATION", "GEOGRAPHIC_CONTEXT", "LANGUAGE", "UNMAPPED"]
_RANK = {l: i for i, l in enumerate(PRIORITY)}


def expand_to_words(text: str, s: int, e: int) -> tuple[int, int]:
    while s > 0 and text[s - 1].isalnum() and text[s].isalnum():
        s -= 1
    while e < len(text) and text[e].isalnum() and text[e - 1].isalnum():
        e += 1
    return s, e


def reconcile(text: str, spans: list[Prediction], expand: bool = True) -> list[Prediction]:
    items = []
    for p in spans:
        s, e = (expand_to_words(text, p.start, p.end) if expand else (p.start, p.end))
        if e > s:
            items.append((s, e, p))
    items.sort(key=lambda x: (x[0], -x[1]))
    merged: list[list] = []
    for s, e, p in items:
        if merged and s <= merged[-1][1]:
            cur = merged[-1]
            cur[1] = max(cur[1], e)
            cur[2].append(p)
        else:
            merged.append([s, e, [p]])
    out = []
    for s, e, ps in merged:
        best = min(ps, key=lambda p: _RANK.get(p.canonical_label, len(PRIORITY)))
        sources = sorted({p.native_label.split(":")[0] if ":" in p.native_label else "model" for p in ps})
        scores = [p.score for p in ps if p.score is not None]
        out.append(Prediction(start=s, end=e, text=text[s:e], native_label="+".join(sources),
                              canonical_label=best.canonical_label, score=max(scores) if scores else None))
    return out
