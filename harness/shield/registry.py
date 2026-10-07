"""Registry matching: member / provider / staff / facility / employer / contact names and
known identifiers from Cadence's registries for the document's member.

Matching is case- and accent-insensitive on word tokens. Person-name tokens also match
with a small edit distance (ASR / accent spelling variation). Identifiers match on
normalised digit strings, including spoken digits.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from harness.schema import Prediction
from harness.shield.spoken import digit_runs

_TOK = re.compile(r"[A-Za-zÀ-ÿ0-9'’]+")
# Registry name tokens that are also common words: match only when capitalised in text.
COMMON_WORDS = {"price", "brooks", "hughes", "kim", "may", "will", "bill", "rose", "grant", "lane", "park",
                "washington", "jefferson", "lincoln", "king", "young", "white", "green", "black", "hall",
                "mercy", "riverside", "valley", "lakeshore", "cedar", "grove", "general", "north", "south",
                "pat", "dot", "sam", "dan", "jim", "mike", "bob", "walt", "jess", "lena", "tere", "target",
                "amazon", "general", "memorial"}
NICK = {"robert": ["bob", "rob", "bobby"], "william": ["bill", "will", "billy"], "patricia": ["pat", "patty"],
        "michael": ["mike"], "james": ["jim", "jimmy"], "daniel": ["dan", "danny"], "samuel": ["sam"],
        "dorothy": ["dot", "dottie"], "walter": ["walt"], "jessica": ["jess"], "elena": ["lena"], "teresa": ["tere"]}
LABELS = {"member": "NAME", "provider": "PROVIDER_NAME", "staff": "CARE_TEAM_MEMBER", "facility": "FACILITY_NAME",
          "employer": "EMPLOYER", "contact": "RELATIVE_NAME"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


PARTICLES = {"de", "del", "la", "las", "los", "el", "y", "e", "da", "das", "do", "dos", "van", "von", "der",
             "den", "san", "santa", "santo", "st", "mc", "mac", "bin", "al", "le", "di", "du"}


@dataclass
class RegistryParams:
    """Matching policy (configs/phi/recognizers/registry.yaml `matching`). Defaults = validated behaviour."""
    fuzzy: bool = True
    fuzzy_min_len: int = 5                 # reference tokens shorter than this never fuzzy-match
    fuzzy_max_edits: int = 1
    fuzzy_max_edits_long: int = 2          # for reference tokens >= fuzzy_long_len
    fuzzy_long_len: int = 8
    single_token_requires_capital: bool = True
    partial_token_min_len: int = 3
    nicknames: dict = field(default_factory=lambda: {k: list(v) for k, v in NICK.items()})
    particles: set = field(default_factory=lambda: set(PARTICLES))
    common_words: set = field(default_factory=lambda: set(COMMON_WORDS))

    @classmethod
    def from_config(cls, raw: dict | None) -> "RegistryParams":
        raw = dict(raw or {})
        for k in ("particles", "common_words"):
            if k in raw:
                raw[k] = set(raw[k])
        return cls(**raw)


DEFAULT_PARAMS: "RegistryParams"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower().replace("’", "'"))
    return "".join(c for c in s if not unicodedata.combining(c))


def _lev(a: str, b: str, cap: int) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _fuzzy_ok(tok: str, ref: str, p: "RegistryParams | None" = None) -> bool:
    p = p or DEFAULT_PARAMS
    if tok == ref:
        return True
    if len(ref) >= p.fuzzy_min_len and len(tok) >= p.fuzzy_min_len - 1:
        cap = p.fuzzy_max_edits_long if len(ref) >= p.fuzzy_long_len else p.fuzzy_max_edits
        return _lev(tok, ref, max(p.fuzzy_max_edits, p.fuzzy_max_edits_long)) <= cap
    return False


def build_terms(reg: dict[str, Any], rp: "RegistryParams | None" = None) -> list[tuple[list[str], str, str, bool]]:
    """(token sequence, canonical label, source kind, fuzzy-allowed)."""
    rp = rp or DEFAULT_PARAMS
    terms: list[tuple[list[str], str, str, bool]] = []

    def add(name: str, kind: str, partial_tokens: bool, fuzzy: bool):
        toks = [norm(t) for t in _TOK.findall(name)]
        if not toks:
            return
        lab = LABELS[kind]
        terms.append((toks, lab, kind, fuzzy))
        if partial_tokens:
            for t in toks:
                if len(t) >= rp.partial_token_min_len and t not in rp.particles:
                    terms.append(([t], lab, kind, fuzzy))
            if kind == "member" and toks[0] in rp.nicknames:
                for n in rp.nicknames[toks[0]]:
                    terms.append(([n], lab, kind, False))

    m = reg.get("member") or {}
    names = m.get("names") or ([m["name"]] if m.get("name") else [])
    for n in names:
        add(n, "member", True, True)
    for p in reg.get("providers", []):
        add(p["name"] if isinstance(p, dict) else p, "provider", True, True)
    for s in reg.get("staff", []):
        add(s, "staff", True, True)
    for f in reg.get("facilities", []):
        add(f["name"], "facility", False, False)
        for a in f.get("aliases", []):
            add(a, "facility", False, False)
    for e in reg.get("employers", []):
        add(e, "employer", False, False)
    for c in reg.get("contacts", []):
        add(c, "contact", True, True)
    return terms


def _identifier_values(reg: dict[str, Any]) -> list[tuple[str, str]]:
    m = reg.get("member") or {}
    vals = [(d, "MEMBER_ID") for d in m.get("member_ids", [])]
    if m.get("mrn"):
        vals.append((m["mrn"], "MRN"))
    if m.get("phone"):
        vals.append((m["phone"], "PHONE"))
    for p in reg.get("providers", []):
        if isinstance(p, dict) and p.get("npi"):
            vals.append((p["npi"], "NPI"))
    for v in reg.get("provider_ids", []):
        vals.append((v, "NPI"))
    return [(re.sub(r"\D", "", v), lab) for v, lab in vals if len(re.sub(r"\D", "", v)) >= 5] + \
           [(v, lab) for v, lab in vals if re.search(r"[A-Za-z]", v) and len(v) >= 6]


def _dob_variants(iso: str) -> list[str]:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return []
    mon = MONTHS[d.month - 1]
    suf = "th" if 10 <= d.day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    out = [d.strftime("%m/%d/%Y"), f"{d.month}/{d.day}/{d.year}", d.strftime("%m-%d-%y"),
           f"{mon} {d.day}, {d.year}", d.strftime("%d/%m/%Y"), f"{d.month}/{d.day}/{d.year % 100:02d}",
           # spoken forms after ASR normalisation ("twenty eighth March nineteen eighty five" -> "28th March 1985")
           f"{d.day}{suf} {mon} {d.year}", f"{d.day}{suf} of {mon} {d.year}", f"{mon} {d.day}{suf} {d.year}",
           f"{mon} {d.day}{suf}, {d.year}", f"{mon} {d.day} {d.year}", f"{d.day} {mon} {d.year}"]
    return list(dict.fromkeys(out))


def match(text: str, reg: dict[str, Any] | None, rp: "RegistryParams | None" = None) -> list[Prediction]:
    if not reg:
        return []
    out: list[Prediction] = []
    letters = [c for c in text if c.isalpha()]
    lowercase_doc = bool(letters) and sum(c.isupper() for c in letters) / len(letters) < 0.01   # ASR output
    toks = []
    for mm in _TOK.finditer(text):
        s0, e0, w = mm.start(), mm.end(), mm.group()
        if re.search(r"['’]s$", w, re.I):          # possessive: "Brooks's" -> "Brooks"
            e0, w = e0 - 2, w[:-2]
        toks.append((s0, e0, norm(w), w))
    rp = rp or DEFAULT_PARAMS
    for seq, lab, kind, fuzzy in build_terms(reg, rp):
        n = len(seq)
        for i in range(len(toks) - n + 1):
            ok, exact = True, True
            for k in range(n):
                t = toks[i + k][2]
                if t == seq[k]:
                    continue
                if fuzzy and rp.fuzzy and _fuzzy_ok(t, seq[k], rp):
                    exact = False
                    continue
                ok = False
                break
            if not ok:
                continue
            if n == 1 and rp.single_token_requires_capital and not toks[i][3][:1].isupper() and \
                    (seq[0] in rp.common_words or not lowercase_doc):
                continue   # single-token matches must be capitalised (unless the whole doc is lowercase ASR)
            s, e = toks[i][0], toks[i + n - 1][1]
            out.append(Prediction(start=s, end=e, text=text[s:e],
                                  native_label=f"registry:{kind}:{'exact' if exact else 'fuzzy'}{'' if n > 1 else ':single_token'}",
                                  canonical_label=lab, score=(1.0 if n > 1 else 0.85) * (1.0 if exact else 0.9)))
    # identifiers: digit-normalised exact match (written or spoken)
    digit_spans = [(m.start(), m.end(), re.sub(r"\D", "", m.group())) for m in re.finditer(r"\d[\d\s().\-/]{3,25}\d", text)]
    digit_spans += digit_runs(text, 5)
    for val, lab in _identifier_values(reg):
        if val.isdigit():
            for s, e, d in digit_spans:
                if val in d:
                    out.append(Prediction(start=s, end=e, text=text[s:e], native_label="registry:identifier",
                                          canonical_label=lab, score=1.0))
        else:
            for mm in re.finditer(re.escape(val), text, re.I):
                out.append(Prediction(start=mm.start(), end=mm.end(), text=mm.group(), native_label="registry:identifier",
                                      canonical_label=lab, score=1.0))
    m = reg.get("member") or {}
    literal = [(m.get("email"), "EMAIL"), (m.get("address"), "ADDRESS"), (m.get("city"), "CITY"), (m.get("zip"), "ZIP")]
    literal += [(a, "ADDRESS") for a in (m.get("addresses") or [])]
    literal += [(v, "DOB") for v in _dob_variants(m.get("dob", ""))]
    for val, lab in literal:
        if val and len(val) >= 4:
            for mm in re.finditer(re.escape(val), text, re.I):
                out.append(Prediction(start=mm.start(), end=mm.end(), text=mm.group(), native_label="registry:record",
                                      canonical_label=lab, score=1.0))
    return out


DEFAULT_PARAMS = RegistryParams()
