"""Conversational / ASR recognizers (opt-in; developed ONLY on cadence_dev - see docs/rule_changelog.md).

Each recognizer targets a speech pattern the written-text rules cannot see:
  conv_spoken_id      IDs / phones read aloud: letter prefixes (incl. "D as in David", Spanish
                      letter names), digit words, tens ("sesenta y tres", "ninety-one"),
                      fillers and self-corrections inside the run; labelled by nearby context
  conv_partial_phone  "ends in oh one five two", "termina en cero uno dos ocho", "last four ..."
  conv_ordinal_date   "the twenty-third", "Tuesday the fourteenth", "sábado diecisiete",
                      "el lunes", spoken numeric DOBs "four, twelve, ninety-one", Spanish
                      spoken years "del setenta y ocho"
  conv_spoken_address unit / apartment / suite / box / lote numbers, spoken house numbers
                      before a street, Spanish "N de (la calle) X", spoken ZIPs after a state
  conv_spelled_name   letter-by-letter spellings ("R A M A N", "g, h, e, double r") labelled
                      NAME / EMAIL / ADDRESS by context
  conv_language       preferred-language statements ("prefers Spanish", "Idioma preferido: español")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit
from harness.shield.rules import MESES, MONTHS, STATE_NAMES

I = re.IGNORECASE
# ---------------------------------------------------------------------------- number words
UNIT = {"zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
TEEN = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19, "diez": 10, "once": 11, "doce": 12, "trece": 13,
        "catorce": 14, "quince": 15, "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
        "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiún": 21, "veintidós": 22, "veintidos": 22,
        "veintitrés": 23, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiséis": 26,
        "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
        "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
HUND = {"hundred": 100, "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
        "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900,
        "mil": 1000, "thousand": 1000}
_UNITS_RX = "|".join(k for k in UNIT if k != "o")
_NUMWORD = "|".join(sorted(set(UNIT) | set(TEEN) | set(TENS) | set(HUND) - {"o"}, key=len, reverse=True))
NUM_SEQ = rf"(?:(?:{_NUMWORD}|\d+)(?:[\s-]+(?:y\s+|and\s+)?(?:{_NUMWORD}|\d+))*)"

ORD_EN = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
          "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
          "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
          "twentieth": 20, "thirtieth": 30}
_ORD = rf"(?:(?:twenty|thirty)[\s-](?:{'|'.join(list(ORD_EN)[:9])})|{'|'.join(ORD_EN)})"
_ORD_NUM = r"(?:\d{1,2}(?:st|nd|rd|th))"
WEEKDAYS_EN = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)"
DIAS = r"(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)"
ES_DAY = rf"(?:{'|'.join(k for k in list(TEEN) + ['uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve', 'primero'] if k)}|treinta(?: y uno)?|\d{{1,2}})"

# ---------------------------------------------------------------------------- shared helpers
LETTER_ES = {"a", "be", "ce", "de", "e", "efe", "ge", "hache", "i", "jota", "ka", "ele", "eme", "ene", "eñe", "o",
             "pe", "cu", "ere", "erre", "ese", "te", "u", "uve", "ve", "equis", "ye", "zeta", "doble"}
AMBIG = {"a", "i", "e", "o", "u", "y", "de", "be", "te", "ve", "ese", "pe", "ce"}   # also ordinary words
FILLER = {"uh", "um", "eh", "este", "then", "like", "so", "okay", "ok", "it's", "its", "is", "es"}
CORRECTION = re.compile(r"^(?:[\s,.\-…]*(?:sorry|no|perdón|perdon|wait|oh wait|scratch that|i mean|no espera|espera|"
                        r"was it|o sea|digo|actually|my glasses|that's a \w+ at the end)[\s,.\-…!?]*)+$", I)
_CORR_AT = re.compile(r"(?:[\s,.\-…]*(?:sorry|no|perdón|perdon|wait|oh wait|scratch that|i mean|no espera|espera|was it|"
                      r"o sea|digo|actually|let me see|hold on|then)\b[\s,.\-…!?]*)+", I)
_TOK = re.compile(r"[A-Za-zÀ-ÿñÑ]+(?:'[a-z]+)?|\d+")
_SEP_OK = re.compile(r"^[\s,.\-–—…/#]*(?:dash|guion|guión|hyphen)?[\s,.\-–—…/#]*$", I)


def _ctx(text: str, start: int, n: int = 90) -> str:
    """Preceding context without speaker labels ("MEMBER:" is not a member-ID cue)."""
    return re.sub(r"(?:^|\n)[A-Z_]+:", " ", text[max(0, start - n):start]).lower()


ID_LABELS = [
    (r"\b(?:phone|cell|number to reach|call(?:back)? (?:me|you|her|him)? ?at|text me|tel[eé]fono|celular|n[uú]mero (?:de )?(?:tel|cel)|"
     r"best number|contact number|direct line|reach (?:me|her|him)|línea)\b", "PHONE"),
    (r"\b(?:m ?r ?n|medical record|record number|expediente|historia cl[ií]nica|n ?h ?c)\b", "MRN"),
    (r"\b(?:member (?:id|i d|number)|member\b|medicaid|medicare|card|tarjeta|n[uú]mero de (?:miembro|socio)|"
     r"insurance|seguro|eligibility|ahcccs|subscriber|policy)\b", "MEMBER_ID"),
    (r"\b(?:account|cuenta)\b", "ACCOUNT_ID"),
    (r"\bn ?p ?i\b", "NPI"),
]


GENERIC_ID_KEYWORDS = (r"\b(?:confirmation|order|reference|ticket|claim|case|work order|complaint|serial|tracking|"
                       r"prescription|rx|service number|gate code|code|n[uú]mero de (?:receta|reclamo|caso|orden))\b")
DOB_CUES = r"date of birth|birth ?date|birthday|born|d\.?o\.?b\.?|fecha de nacimiento|naci[óo]|cumpleaños"
UNIT_KEYWORDS = (r"apartment|apt\.?|unit|suite|ste\.?|lote|lot|box|p\.? ?o\.? box|building|bldg\.?|apartamento|apto\.?|"
                 r"depto\.?|piso|trailer|space")
UNIT_CONTEXT = r"address|live|house|apartment|street|calle|deliver|ship|mail|drive|route|road|avenue|lane"
LANGUAGE_NAMES = ["Spanish", "English", "Haitian Creole", "Creole", "Kreyòl", "Portuguese", "Mandarin", "Cantonese", "Chinese",
                  "Vietnamese", "Tagalog", "Korean", "Arabic", "Russian", "Polish", "French", "Somali", "Amharic", "Tamil",
                  "Hindi", "Urdu", "Bengali", "Punjabi", "Gujarati", "Farsi", "Dari", "Pashto", "Nepali", "Burmese", "Hmong",
                  "Khmer", "Lao", "Swahili", "Quechua", "K'iche'", "Mam", "ASL", "American Sign Language",
                  "espa[nñ]ol", "ingl[eé]s", "portugu[eé]s", "criollo(?: haitiano)?", "franc[eé]s", "chino", "mandar[ií]n",
                  "quechua", "k'iche'"]
LANGUAGE_CUES = (r"prefer\w*|pref\.? ?lang\w*|preferred language|language(?: at home)?(?: is)?(?: mostly)?|"
                 r"idioma(?: preferido)?|lengua(?: materna)?|prefiere|prefer[ií]a|better in|mejor en")


@dataclass
class ConvParams:
    """Keyword tables of the conversational rules - configurable (configs/phi/recognizers/contextual.yaml
    `contextual_params`). Defaults reproduce the dev-validated behaviour exactly."""
    id_labels: list = field(default_factory=lambda: [list(x) for x in ID_LABELS])
    generic_id_keywords: str = GENERIC_ID_KEYWORDS
    dob_cues: str = DOB_CUES
    unit_keywords: str = UNIT_KEYWORDS
    unit_context: str = UNIT_CONTEXT
    language_names: list = field(default_factory=lambda: list(LANGUAGE_NAMES))
    language_cues: str = LANGUAGE_CUES

    def __post_init__(self) -> None:
        self.id_rx = [(re.compile(rx, I), lab) for rx, lab in self.id_labels]
        self.generic_rx = re.compile(self.generic_id_keywords, I)
        self.dob_rx = re.compile(rf"(?:{self.dob_cues})\W*(?:\w+\W+){{0,4}}$", I)
        self.unit_kw = self.unit_keywords
        self.unit_ctx_rx = re.compile(self.unit_context, I)
        self.langs_rx = re.compile(rf"\b(?:{'|'.join(self.language_names)})\b", I)
        self.lang_ctx_rx = re.compile(rf"(?:{self.language_cues})\W*(?:\w+\W+){{0,3}}$", I)

    @classmethod
    def from_config(cls, raw: Optional[dict]) -> "ConvParams":
        return cls(**(raw or {}))


def id_label(text: str, start: int, p: Optional["ConvParams"] = None) -> str:
    p = p or DEFAULT_PARAMS
    c = _ctx(text, start, 120)
    best, pos = "OTHER_UNIQUE_ID", -1
    for rx, lab in p.id_rx:
        for m in rx.finditer(c):
            if m.end() > pos:
                best, pos = lab, m.end()
    # generic identifier words closer than the specific keyword win
    for m in p.generic_rx.finditer(c):
        if m.end() > pos:
            best, pos = "OTHER_UNIQUE_ID", m.end()
    return best


# ---------------------------------------------------------------------------- conv_spoken_id
def _classify(tok: str, nxt: str | None) -> tuple[str, int]:
    """('digit'|'letter'|'num2'|'mult'|'dash'|'filler'|'', digits contributed)"""
    lw = tok.lower()
    if tok.isdigit():
        return "digit", len(tok)
    if lw in UNIT and lw != "o":
        return "digit", 1
    if lw in TEEN:
        return "num2", 2
    if lw in TENS:
        return "num2", 2
    if lw in ("double", "triple", "doble"):
        return "mult", 0
    if lw in ("dash", "guion", "guión", "hyphen"):
        return "dash", 0
    if lw in FILLER:
        return "filler", 0
    if len(tok) == 1 and tok.isalpha():
        return ("letter_es" if tok in AMBIG else "letter"), 0
    if lw in LETTER_ES:
        return "letter_es", 0
    return "", 0


def spoken_id_runs(text: str, min_digits: int = 4) -> list[tuple[int, int, int, int]]:
    """(start, end, digits, letters) runs of read-aloud identifiers."""
    toks = [(m.start(), m.end(), m.group()) for m in _TOK.finditer(text)]
    out = []
    i = 0
    while i < len(toks):
        j, digits, letters, last_good, start, spoken = i, 0, 0, None, None, 0
        while j < len(toks):
            s, e, w = toks[j]
            if last_good is not None:
                gap = text[toks[last_good][1]:s]
                if not _SEP_OK.match(gap):
                    # self-correction bridge ("..., sorry, scratch that, nine one three")
                    if not (CORRECTION.match(gap) or CORRECTION.match(gap + " ")):
                        break
            kind, nd = _classify(w, toks[j + 1][2] if j + 1 < len(toks) else None)
            # "X as in Word" / "X like Word" / "D de dedo"
            if kind in ("letter", "letter_es") and j + 3 < len(toks) and re.fullmatch(
                    r"\s*,?\s*(?:as in|like|for|de|como)\s+(?:(?:um|uh),?\s+)?", text[e:toks[j + 2][1] if toks[j + 1][2].lower() == "as" else toks[j + 1][1]] + " ", I):
                pass
            m_as = re.match(r"\s*,?\s*(?:as\s+in|like|como|de)\s+(?:(?:um|uh),?\s+)?([A-Za-zÀ-ÿ]+)", text[e:e + 30], I) if kind in ("letter", "letter_es") else None
            if kind in ("letter", "letter_es") and m_as and m_as.group(1)[:1].lower() == w[:1].lower() and len(m_as.group(1)) > 2:
                letters += 1
                start = s if start is None else start
                e2 = e + m_as.end()
                while j + 1 < len(toks) and toks[j + 1][0] < e2:
                    j += 1
                last_good = j
                j += 1
                continue
            if kind == "letter_es" and w.lower() in AMBIG and start is None:
                # ambiguous Spanish/English word may only START a run if an unambiguous id token follows
                nk = _classify(toks[j + 1][2], None)[0] if j + 1 < len(toks) else ""
                if nk not in ("digit", "num2", "letter") or (j + 1 < len(toks) and not _SEP_OK.match(text[e:toks[j + 1][0]])):
                    break
            if kind == "letter" and w in ("a", "I") and start is None:
                break
            if kind in ("digit", "num2"):
                digits += nd
                spoken += not w.isdigit()
                # "sesenta y tres": swallow the "y <unit>"
                if kind == "num2" and j + 2 < len(toks) and toks[j + 1][2].lower() in ("y", "and") and toks[j + 2][2].lower() in UNIT:
                    j += 2
                elif kind == "num2" and j + 1 < len(toks) and text[e:toks[j + 1][0]] == "-" and toks[j + 1][2].lower() in UNIT:
                    j += 1
            elif kind in ("letter", "letter_es"):
                letters += w.lower() not in AMBIG
            elif kind in ("mult", "dash"):
                pass
            elif kind == "filler":
                if start is None:
                    break
                j += 1
                continue
            else:
                if start is not None:
                    cm = _CORR_AT.match(text, toks[last_good][1])
                    if cm and cm.end() > s:
                        nxt = next((k for k in range(j, len(toks)) if toks[k][0] >= cm.end()), None)
                        if nxt is not None and _classify(toks[nxt][2], None)[0] in ("digit", "num2", "letter"):
                            j = nxt
                            continue
                break
            start = s if start is None else start
            last_good = j
            j += 1
        if start is not None and last_good is not None:
            # trim trailing ambiguous letters / fillers
            k = last_good
            while k > i and _classify(toks[k][2], None)[0] in ("letter_es", "filler", "dash", "mult") and toks[k][2].lower() in AMBIG | FILLER | {"dash", "guion", "double", "doble"}:
                k -= 1
            end = toks[k][1]
            after, before = text[end:end + 25], text[max(0, start - 14):start]
            is_time = re.match(r"\s*(?:a\.?\s?m\b|p\.?\s?m\b|o'?clock|de la (?:mañana|tarde|noche)|en punto|y media|in the (?:morning|afternoon|evening))", after, I) \
                or re.search(r"(?:\ba las|\ba la|\bat|\bfrom|\bdesde las|\bde las)\s*$", before, I)
            if spoken >= 2 and not is_time and (digits >= min_digits or (digits >= 3 and letters >= 2)):
                out.append((start, end, digits, letters))
                i = k + 1
                continue
        i += 1
    return out


def conv_spoken_id(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    out = []
    for s, e, nd, nl in spoken_id_runs(text):
        if nl == 0 and nd < 4:
            continue
        lab = id_label(text, s, p)
        if nl == 0 and lab == "OTHER_UNIQUE_ID" and nd < 7:
            continue   # bare short digit runs without an identifier keyword: too ambiguous (doses, vitals)
        out.append((s, e, lab, 0.85 if nl else 0.8, f"spoken_id:{lab.lower()}"))
    return out


# ---------------------------------------------------------------------------- conv_partial_phone
_PARTIAL = re.compile(rf"\b(?:ends? (?:in|with)|ending in|termina en|que termina en|last (?:four|4)(?: digits)?(?: of [\w\s]{{0,25}}?)?(?: is| are| it's|,|:)?|"
                      rf"(?:los )?[uú]ltimos (?:cuatro|4)(?: d[ií]gitos)?(?: son| es|,|:)?)\s*(?:(?:um|uh|eh),?\s*)?"
                      rf"(?P<n>(?:(?:{_UNITS_RX}|\d)[\s,\-]*){{4}})", I)


def conv_partial_phone(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    out = []
    for m in _PARTIAL.finditer(text):
        s, e = m.start("n"), m.end("n")
        while e > s and text[e - 1] in " ,-":
            e -= 1
        lab = id_label(text, m.start(), p)
        lab = "PHONE" if lab in ("OTHER_UNIQUE_ID",) and re.search(r"number|n[uú]mero|tel", _ctx(text, m.start())) else lab
        out.append((s, e, lab, 0.8, "partial_four"))
    return out


# ---------------------------------------------------------------------------- conv_ordinal_date
_ORD_STOP = re.compile(r"^\s*(?:time|times|one|ones|dose|doses|floor|step|steps|thing|things|shot|round|week|weeks|"
                       r"day of|visit|session|person|grade|try|call|line|page|option|choice|child|kid|baby|pill|refill|month)\b", I)


def conv_ordinal_date(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    DOB_CTX = (p or DEFAULT_PARAMS).dob_rx
    out = []
    months = rf"(?:{MONTHS}|{MESES})"
    # "the twenty-third", "Tuesday the fourteenth", "on the 23rd", "October twenty-third"
    for m in re.finditer(rf"\b(?:(?P<wd>{WEEKDAYS_EN})\s+)?(?:(?P<mon>{months})\s+|(?P<the>the|on the|by the|of the)\s+)"
                         rf"(?P<o>{_ORD}|{_ORD_NUM})\b", text, I):
        if not (m.group("wd") or m.group("mon")) and _ORD_STOP.match(text[m.end():]):
            continue
        if not (m.group("wd") or m.group("mon")) and m.group("o").lower() in ("first", "second", "third", "1st", "2nd", "3rd") and not (
                re.match(rf"\s*of\s+{months}", text[m.end():], I) or
                re.search(r"\b(?:on|by|until|before|after|starting|due|scheduled for|is|was)\s+the\s*$", text[max(0, m.start() - 20):m.start("o")], I)):
            continue
        s = m.start("wd") if m.group("wd") else (m.start("mon") if m.group("mon") else m.start("o"))
        out.append((s, m.end(), "DATE", 0.8, "ordinal_day"))
    # bare capitalised ordinal answering a date question / after a date ("Twenty-third.")
    for m in re.finditer(rf"(?<=: )(?P<o>{_ORD})(?=[.?!,])", text, I):
        out.append((m.start("o"), m.end("o"), "DATE", 0.7, "ordinal_answer"))
    # Spanish: "sábado diecisiete", "martes veintiuno", "el lunes", "jueves que viene", "el catorce"
    for m in re.finditer(rf"\b(?P<art>el|este|próximo|proximo|el próximo|el proximo)?\s*(?P<d>{DIAS})(?:\s+(?P<n>{ES_DAY})\b|\s+(?P<q>que viene|pasado|de la semana que viene))?", text, I):
        if not (m.group("art") or m.group("n") or m.group("q")):
            continue
        if m.group("art") and m.group("art").lower() == "el" and re.match(r"\s*(?:y|,)\s*(?:el\s+)?" + DIAS, text[m.end():], I):
            pass
        s = m.start("art") if m.group("art") else m.start("d")
        out.append((s, m.end(), "DATE", 0.75, "es_weekday"))
    for m in re.finditer(rf"\b(?:para|el|desde el|hasta el)\s+(?P<n>{ES_DAY})(?=\s*(?:[.,;?]|$|de\s+{MESES}|a las))", text, I):
        out.append((m.start("n"), m.end("n"), "DATE", 0.6, "es_day_number"))
    # spoken numeric DOB after a DOB cue: "four, twelve, ninety-one" / "Eight, nineteen, fifty-eight"
    nw = rf"(?:(?:{'|'.join(TENS)})(?:[\s-](?:{_UNITS_RX}))?|{'|'.join(TEEN)}|{_UNITS_RX})"
    for m in re.finditer(rf"\b(?P<v>{nw}),?\s+{nw},?\s+(?:(?:nineteen|twenty)\s+)?{nw}\b", text, I):
        if DOB_CTX.search(text[max(0, m.start() - 80):m.start()]):
            out.append((m.start(), m.end(), "DOB", 0.85, "spoken_numeric_dob"))
    # Spanish spoken year tail after a date: "once de marzo del setenta y ocho"
    for m in re.finditer(rf"\b(?:{ES_DAY})\s+de\s+(?:{MESES})(?P<y>,?\s+(?:del?|de)\s+{NUM_SEQ})", text, I):
        lab = "DOB" if DOB_CTX.search(text[max(0, m.start() - 80):m.start()]) else "DATE"
        out.append((m.start(), m.end(), lab, 0.85, "es_spoken_year"))
    for m in re.finditer(rf"\b(?:{MONTHS})\s+(?:{_ORD}|{nw})(?P<y>,?\s+(?:nineteen|twenty|two thousand)?\s*{nw})\b", text, I):
        lab = "DOB" if DOB_CTX.search(text[max(0, m.start() - 80):m.start()]) else "DATE"
        out.append((m.start(), m.end(), lab, 0.8, "en_spoken_full_date"))
    return out


# ---------------------------------------------------------------------------- conv_spoken_address
STREET_SFX = (r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Boulevard|Blvd|Way|Place|Pl|Parkway|Pkwy|"
              r"Circle|Terrace|Trail|Highway|Hwy|Loop|Route|Pike)")
CAPW = r"[A-Z][a-záéíóúñ'’]+"


def conv_spoken_address(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    p = p or DEFAULT_PARAMS
    out = []
    unit_val = rf"(?:{NUM_SEQ}(?:[\s,]+(?:[A-Z]\b|be|ce|de)(?:,?\s+[A-Za-z]+ like [a-z]+)?)?|[A-Z]\d*\b|\d+[A-Z]?)"
    for m in re.finditer(rf"\b(?P<k>{p.unit_kw})\b[\s,]*(?:#|number|n[uú]mero)?\s*(?:(?:um|uh|eh),?\s+)?(?P<v>{unit_val})", text, I):
        v = m.group("v")
        if re.match(r"(?i)(?:of|de la|of the)\b", text[m.end():].strip()[:6]) and not re.search(r"\d|[A-Z]\b", v):
            continue   # "a unit of insulin"
        if m.group("k").lower() in ("unit", "box", "lot", "space", "building") and not p.unit_ctx_rx.search(
                _ctx(text, m.start(), 160)):
            continue
        out.append((m.start(), m.end(), "ADDRESS", 0.8, f"unit:{m.group('k').lower()}"))
    # spoken house number + street ("twenty-six oh one Marigold Lane", "Seventeen County Road two twelve")
    for m in re.finditer(rf"\b(?P<n>{NUM_SEQ})\s+(?:(?:North|South|East|West|N|S|E|W|Northwest|Noroeste|Old)\.?\s+)?"
                         rf"(?P<st>(?:{CAPW}\s+){{0,3}}{STREET_SFX}\b(?:\s+{NUM_SEQ})?|County Road\s+{NUM_SEQ}|(?:Rural Route|RR)\s+{NUM_SEQ}"
                         rf"(?:,?\s+box\s+{NUM_SEQ})?)", text):
        if re.fullmatch(r"(?i)(?:one|a)", m.group("n").strip()):
            continue
        out.append((m.start(), m.end(), "ADDRESS", 0.85, "spoken_house_number"))
    for m in re.finditer(rf"\b(?P<n>{NUM_SEQ})\s+(?P<dir>(?:North|South|East|West)\s+)?(?P<st>{CAPW}(?:\s+{CAPW})?)\b", text):
        ctx = _ctx(text, m.start(), 100)
        if re.search(r"address|live|living|house|moved|staying|at the|pick ?up|deliver|direcci[oó]n|vive|casa", ctx) and \
                re.search(rf"(?:{'|'.join(TENS)}|{'|'.join(TEEN)}|hundred|\boh\b|\d{{3,}})", m.group("n"), I):
            out.append((m.start(), m.end(), "ADDRESS", 0.7, "spoken_house_number_ctx"))
    # Spanish: "cuatrocientos uno de Lake Trafford Road", "el mil doscientos diez", "cinco seis cero dos de la calle Sesenta y Dos"
    for m in re.finditer(rf"\b(?P<n>{NUM_SEQ})\s+de\s+(?:la\s+)?(?:(?:calle|avenida|ave\.?|carretera|camino)\s+)?(?P<st>(?:{CAPW}|{NUM_SEQ})(?:\s+(?:y\s+)?(?:{CAPW}|{NUM_SEQ})){{0,3}})", text, I):
        if not re.match(r"[A-ZÁÉÍÓÚ]|calle|avenida|carretera|camino", text[m.start("st"):m.start("st") + 9]) and \
                not re.search(r"calle|avenida|carretera", text[m.start():m.start("st")], I):
            continue
        if re.match(rf"(?i)(?:{MESES})\b", m.group("st")):
            continue   # "once de marzo"
        out.append((m.start(), m.end(), "ADDRESS", 0.8, "es_house_number"))
    for m in re.finditer(rf"\b(?P<k>calle|avenida|carretera|camino|boulevard|bulevar)\s+(?P<st>{CAPW}(?:\s+(?:{CAPW}|y))*|{NUM_SEQ})"
                         rf"(?:,?\s+(?:el\s+)?(?P<n>{NUM_SEQ}))?", text, I):
        out.append((m.start(), m.end(), "ADDRESS", 0.75, "es_street"))
    # spoken ZIP after a state name: "Massachusetts, oh two seven two four"
    for m in re.finditer(rf"(?:{STATE_NAMES}),?\s+(?P<z>(?:(?:{_UNITS_RX})[\s,\-]*){{5}})", text):
        z0, z1 = m.start("z"), m.end("z")
        while z1 > z0 and text[z1 - 1] in " ,-":
            z1 -= 1
        out.append((z0, z1, "ZIP", 0.85, "spoken_zip"))
    return out


# ---------------------------------------------------------------------------- conv_spelled_name
def conv_spelled_name(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    from harness.phi.normalizers.spoken import spelled_names as spelled_letters
    out = []
    id_spans = spoken_id_runs(text)
    for r in spelled_letters(text):
        if len(r.value) < 4:
            continue
        if any(a <= r.start < b or a < r.end <= b for a, b, _, _ in id_spans):
            continue   # part of a read-aloud identifier
        before, after = _ctx(text, r.start, 60), text[r.end:r.end + 40].lower()
        if re.search(r"\b(?:at|arroba)\b", after[:25]) or re.search(r"e-?mail|correo", before):
            lab = "EMAIL"
        elif re.search(r"street|st\.|avenue|road|lane|calle|avenida|address|direcci", before + after):
            lab = "ADDRESS"
        else:
            lab = "NAME"
        out.append((r.start, r.end, lab, 0.8, f"spelled:{lab.lower()}"))
    return out


# ---------------------------------------------------------------------------- conv_language
def conv_language(text: str, p: Optional[ConvParams] = None) -> list[tuple[int, int, str, float, str]]:
    p = p or DEFAULT_PARAMS
    out = []
    for m in p.langs_rx.finditer(text):
        pre = re.split(r"[.?!\n]", text[max(0, m.start() - 45):m.start()])[-1]
        turn_answer = re.search(r"(?:^|\n)[A-Z_]+:\s*$", text[max(0, m.start() - 20):m.start()]) and re.match(r"\s*[.!,]", text[m.end():])
        if re.search(r"int[eé]rprete|interpreter", pre, I) and not re.search(r"prefer|prefiere", pre, I):
            continue   # interpreter options are not the member's stated preference
        if re.search(r"-?speaking|-speaker", text[m.end():m.end() + 10], I):
            if not re.search(r"prefer", pre, I):
                continue   # "a Spanish-speaking nurse" describes staff, not the member
        if re.search(r"\b(?:o|or|y|and)\s*$", pre, I) and re.search(r"int[eé]rprete|interpreter", pre, I):
            continue   # "intérprete de quechua o español": a list of options
        if p.lang_ctx_rx.search(pre) or turn_answer or re.search(r"\b(?:send|mail|write|mand\w+|envi\w+)\b[^.?!]{0,40}\b(?:in|en)\s*$", pre, I):
            out.append((m.start(), m.end(), "LANGUAGE", 0.8, "preferred_language"))
    return out


# ---------------------------------------------------------------------------- recognizer wrapper
DEFAULT_PARAMS = ConvParams()
FUNCS: dict[str, Callable[..., list[tuple[int, int, str, float, str]]]] = {
    "conv_spoken_id": conv_spoken_id, "conv_partial_phone": conv_partial_phone, "conv_ordinal_date": conv_ordinal_date,
    "conv_spoken_address": conv_spoken_address, "conv_spelled_name": conv_spelled_name, "conv_language": conv_language,
}
CONV_ENTITIES = {"conv_spoken_id": ["PHONE", "MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID"],
                 "conv_partial_phone": ["PHONE", "MEMBER_ID"], "conv_ordinal_date": ["DATE", "DOB"],
                 "conv_spoken_address": ["ADDRESS", "ZIP"], "conv_spelled_name": ["NAME", "EMAIL", "ADDRESS"],
                 "conv_language": ["LANGUAGE"]}


class ConversationalRecognizer(PHIRecognizer):
    source = EvidenceSource.contextual_rule

    def __init__(self, recognizer_id: str, language: str = "en", version: str = "1", function: Optional[str] = None,
                 emits: Optional[list[str]] = None, params: Optional[ConvParams] = None) -> None:
        fn_name = function or recognizer_id
        super().__init__(recognizer_id, emits or CONV_ENTITIES[fn_name], language, version)
        self.fn = FUNCS[fn_name]
        self.params = params or DEFAULT_PARAMS

    def hits(self, text: str) -> list[RawHit]:
        allowed = set(self.supported_entities)
        return [RawHit(lab, s, e, sc, ScoreType.contextual_rule, rule_id=rid)
                for s, e, lab, sc, rid in self.fn(text, self.params) if e > s and lab in allowed]
