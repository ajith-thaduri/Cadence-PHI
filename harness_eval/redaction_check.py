"""Independent redaction validation: judge the REDACTED OUTPUT, not the detections.

For one document:
  coverage       original characters inside a Replacement's original range (what the output actually hides)
  output check   each replacement's output slice equals its token and contains no gold core character
  re-scan        verbatim and normalised (digit / case-folded) search of every gold surface in the sanitized
                 text - catches leaks through another mention or an operator bug
  must_not_redact anchors that are not PHI must still be present
  round trip     vault.restore(sanitized) == original (TOKENIZE / PSEUDONYMIZE)
  derivation     tokens must not contain any gold surface (45 CFR 164.514(c))
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from harness.phi.contracts import RedactionResponse


@dataclass
class RedactionCheck:
    covered: set[int]
    verbatim_leaks: list[str] = field(default_factory=list)          # annotation ids
    normalized_leaks: list[str] = field(default_factory=list)
    output_slice_errors: int = 0
    must_not_redact_violations: list[str] = field(default_factory=list)
    roundtrip_ok: Optional[bool] = None
    derived_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"verbatim_leaks": len(self.verbatim_leaks), "normalized_leaks": len(self.normalized_leaks),
                "output_slice_errors": self.output_slice_errors,
                "must_not_redact_violations": len(self.must_not_redact_violations), "roundtrip_ok": self.roundtrip_ok,
                "derived_tokens": self.derived_tokens}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _fold(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def check(text: str, rr: RedactionResponse, gold: list[tuple[str, int, int]], must_not_redact: list[tuple[int, int, str]],
          vault: Any = None) -> RedactionCheck:
    """gold: (annotation_id, start, end) of PHI entities; must_not_redact: (start, end, label)."""
    covered: set[int] = set()
    out = RedactionCheck(covered=covered)
    for rep in rr.replacements:
        covered.update(range(rep.original_start, rep.original_end))
        if rr.sanitized_text[rep.output_start:rep.output_end] != rep.replacement:
            out.output_slice_errors += 1
        for _, s, e in gold:
            surf = text[s:e]
            if len(_fold(surf)) >= 4 and _fold(surf) in _fold(rep.replacement):
                out.derived_tokens += 1
    san = rr.sanitized_text
    san_fold = _fold(san)
    digit_runs = {_digits(m.group()) for m in re.finditer(r"\d[\d\s().\-/]{3,}\d", san)}
    for aid, s, e in gold:
        surf = re.sub(r"\s+", " ", text[s:e].strip())
        if len(re.sub(r"\W", "", surf)) >= 4 and surf.lower() in san.lower():
            out.verbatim_leaks.append(aid)
            continue
        d = _digits(surf)
        if len(d) >= 5 and any(d in run for run in digit_runs):
            out.normalized_leaks.append(aid)
        elif len(_fold(surf)) >= 6 and not d and _fold(surf) in san_fold:
            out.normalized_leaks.append(aid)
    for s, e, label in must_not_redact:
        if any(i in covered for i in range(s, e)):
            out.must_not_redact_violations.append(label)
    if vault is not None and rr.vault_id:
        try:
            out.roundtrip_ok = vault.restore(san) == text
        except Exception:  # noqa: BLE001
            out.roundtrip_ok = False
    return out
