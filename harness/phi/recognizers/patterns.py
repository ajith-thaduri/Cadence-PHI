"""Declarative pattern recognizers (configs/phi/recognizers/patterns.yaml) and named validators.

A pattern recognizer is fully described by configuration: entity, regexes (with optional capture
group and per-pattern `requires_context`), named validators and context words. Adding a deterministic
entity therefore needs no code. Validators are the ONLY code hook and are referenced by name:

  luhn          Luhn checksum over the digits
  npi_luhn      NPI check digit (Luhn with the 80840 prefix)
  ssn_valid     SSA structural rules (area != 000/666/9xx, group != 00, serial != 0000)
  ipv4_valid    every octet 0-255, not a version-like string
  vin_checksum  ISO 3779 / NHTSA check digit (position 9)
  age_over_89   numeric value 90..120
  iban_mod97    ISO 13616 IBAN check digits (mod 97 == 1), country-length 15..34
  latlon_valid  decimal-degree pair: |latitude| <= 90, |longitude| <= 180

A validator returning False drops the candidate. A checksum validator returning True upgrades the
score type to checksum_valid. Validators never see more than the matched span.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit


# function words do not weaken a cue's binding: "location at the time of registration was <value>" is 2 content words
_FUNCTION_WORDS = {"a", "an", "the", "at", "of", "in", "on", "to", "for", "by", "with", "from", "as", "is", "was", "were",
                   "be", "been", "its", "his", "her", "their", "our", "my", "this", "that", "and", "or", "um", "uh"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def luhn(s: str) -> bool:
    d = _digits(s)
    if len(d) < 2:
        return False
    total = 0
    for i, c in enumerate(reversed(d)):
        x = int(c)
        if i % 2 == 1:
            x = x * 2 - 9 if x * 2 > 9 else x * 2
        total += x
    return total % 10 == 0


def npi_luhn(s: str) -> bool:
    d = _digits(s)
    return len(d) == 10 and luhn("80840" + d)


def ssn_valid(s: str) -> bool:
    d = _digits(s)
    if len(d) != 9:
        return False
    area, group, serial = d[:3], d[3:5], d[5:]
    return area not in ("000", "666") and not area.startswith("9") and group != "00" and serial != "0000"


def ipv4_valid(s: str) -> bool:
    if ":" in s:
        return True                       # IPv6 shape already enforced by the regex
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 and (p == "0" or not p.startswith("0")) for p in parts)


_VIN_MAP = {**{str(i): i for i in range(10)}, **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))),
            "P": 7, "R": 9, **dict(zip("STUVWXYZ", range(2, 10)))}
_VIN_W = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_checksum(s: str) -> bool:
    v = s.upper()
    if len(v) != 17:
        return True                       # validator applies to VINs only (plates pass through)
    if any(c not in _VIN_MAP for c in v):
        return False
    rem = sum(_VIN_MAP[c] * w for c, w in zip(v, _VIN_W)) % 11
    return v[8] == ("X" if rem == 10 else str(rem))


def age_over_89(s: str) -> bool:
    m = re.search(r"\d{2,3}", s)
    return bool(m) and 90 <= int(m.group()) <= 120


def iban_mod97(s: str) -> bool:
    v = re.sub(r"\s", "", s).upper()
    if not (15 <= len(v) <= 34 and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", v)):
        return False
    num = "".join(str(int(c, 36)) for c in v[4:] + v[:4])
    return int(num) % 97 == 1


def latlon_valid(s: str) -> bool:
    nums = re.findall(r"-?\d{1,3}\.\d+", s)
    if len(nums) != 2:
        return False
    lat, lon = float(nums[0]), float(nums[1])
    return abs(lat) <= 90 and abs(lon) <= 180


VALIDATORS: dict[str, Callable[[str], bool]] = {"luhn": luhn, "npi_luhn": npi_luhn, "ssn_valid": ssn_valid,
                                                "ipv4_valid": ipv4_valid, "vin_checksum": vin_checksum,
                                                "age_over_89": age_over_89, "iban_mod97": iban_mod97,
                                                "latlon_valid": latlon_valid}
CHECKSUM_VALIDATORS = {"luhn", "npi_luhn", "vin_checksum", "iban_mod97"}


class PatternRecognizer(PHIRecognizer):
    """Config-driven regex recognizer (one entity)."""
    source = EvidenceSource.rule

    def __init__(self, recognizer_id: str, spec: dict[str, Any], language: str = "en") -> None:
        super().__init__(recognizer_id, [spec["entity"]], language, str(spec.get("version", "1")))
        self.entity = spec["entity"]
        unknown = [v for v in spec.get("validators", []) if v not in VALIDATORS]
        if unknown:
            raise ValueError(f"{recognizer_id}: unknown validator(s) {unknown}; known: {sorted(VALIDATORS)}")
        self.validators = [(v, VALIDATORS[v]) for v in spec.get("validators", [])]
        self.patterns = []
        for p in spec["patterns"]:
            flags = 0
            for f in p.get("flags", []):
                flags |= getattr(re, f)
            self.patterns.append((p["name"], re.compile(p["regex"], flags), int(p.get("group", 0)), float(p.get("score", 1.0)),
                                  bool(p.get("requires_context", False))))
        ctx = spec.get("context") or {}
        words = [w for w in ctx.get("words", [])]
        neg = [w for w in ctx.get("negative_words", [])]
        self.ctx_rx = re.compile(r"(?i)(?<![A-Za-z])(?:" + "|".join(re.escape(w) for w in words) + r")(?![A-Za-z])") if words else None
        self.neg_rx = re.compile(r"(?i)(?<![A-Za-z])(?:" + "|".join(re.escape(w) for w in neg) + r")(?![A-Za-z])") if neg else None
        self.window = int(ctx.get("window", 40))
        self.ctx_required = bool(ctx.get("required", False))
        self.side = ctx.get("side", "left")
        self.max_between = ctx.get("max_words_between")

    def _context(self, text: str, s: int, e: int) -> tuple[bool, bool]:
        """Context cues are looked for to the LEFT (a field label precedes its value; looking right lets the next
        field's label claim this value - "passport 512908447, SSN ..."). With max_words_between the nearest cue
        must sit at most that many words before the value."""
        left = text[max(0, s - self.window):s]
        win = left + (" " + text[e:e + self.window // 2] if self.side == "both" else "")
        has = False
        if self.ctx_rx:
            if self.max_between is None:
                has = bool(self.ctx_rx.search(win))
            else:
                last = None
                for last in self.ctx_rx.finditer(left):
                    pass
                has = last is not None and len([w for w in re.findall(r"[A-Za-z0-9]+", left[last.end():])
                                                if w.lower() not in _FUNCTION_WORDS]) <= int(self.max_between)
                if not has and self.side == "both":
                    has = bool(self.ctx_rx.search(text[e:e + self.window // 2]))
        neg = bool(self.neg_rx and self.neg_rx.search(win))
        return has, neg

    def hits(self, text: str) -> list[RawHit]:
        out: list[RawHit] = []
        seen: set[tuple[int, int]] = set()
        for name, rx, grp, score, needs_ctx in self.patterns:
            for m in rx.finditer(text):
                s, e = m.span(grp)
                if e <= s or (s, e) in seen:
                    continue
                span = text[s:e]
                has_ctx, neg_ctx = self._context(text, s, e)
                if neg_ctx or ((needs_ctx or self.ctx_required) and not has_ctx):
                    continue
                st: Optional[ScoreType] = ScoreType.regex_match
                ok = True
                for vname, fn in self.validators:
                    if not fn(span):
                        ok = False
                        break
                    if vname in CHECKSUM_VALIDATORS and (vname != "vin_checksum" or len(span) == 17):
                        st = ScoreType.checksum_valid
                if not ok:
                    continue
                seen.add((s, e))
                out.append(RawHit(self.entity, s, e, score, st, rule_id=f"{self.recognizer_id}:{name}",
                                  metadata={"context_hit": has_ctx}))
        return out
