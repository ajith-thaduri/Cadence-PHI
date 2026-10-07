"""Spoken-form rewrite functions used by the normalizers (harness/phi/normalizers/__init__.py).

Each function returns Replacements on the ORIGINAL text; it never modifies the text.

Normalisers never change the text that is redacted. They build a NormalizedView: a rewritten
copy of the text ("five five five oh one two two" -> "5550122") plus an offset map so any
span found in the view is projected back onto the ORIGINAL characters. Rules and the
registry run on both the original and the view; models always see the original.

Each normaliser is independently switchable (config `normalization.<name>.enabled`).
Development data: cadence_dev only (see docs/rule_changelog.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from harness.shield.spoken import DIGIT_WORDS, digit_runs

I = re.IGNORECASE
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORD_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9}
ORD_TEENS = {"tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
             "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
_U = "|".join(UNITS)
_T = "|".join(TEENS)
_TY = "|".join(TENS)
_OU = "|".join(ORD_UNITS)
_OT = "|".join(ORD_TEENS)
_TWO_DIGIT = rf"(?:(?:{_TY})(?:[\s-](?:{'|'.join(k for k in UNITS if k != 'zero')}))?|{_T})"


@dataclass
class Replacement:
    start: int
    end: int
    value: str
    normalizer: str


# ---- individual normalisers: each returns replacements on the ORIGINAL text ----------------

def spoken_digits(text: str) -> list[Replacement]:
    """Digit-by-digit speech ("five five five, oh one two", "doble cuatro") -> digit string."""
    return [Replacement(s, e, d, "spoken_digits") for s, e, d in digit_runs(text, 3)]


def _two_digit(words: str) -> int | None:
    w = words.lower().replace("-", " ").split()
    if len(w) == 1:
        return TEENS.get(w[0], TENS.get(w[0]))
    if len(w) == 2 and w[0] in TENS and w[1] in UNITS:
        return TENS[w[0]] + UNITS[w[1]]
    return None


_ORDINAL = re.compile(rf"\b(?:(?P<tens>{_TY})[\s-](?P<ou>{_OU})|(?P<ot>{_OT})|(?P<only>{_OU}))\b", I)
_YEAR = re.compile(rf"\b(?P<hi>nineteen|twenty)\s(?:(?P<oh>oh)\s(?P<lo1>{_U})|(?P<lo2>{_TWO_DIGIT}))\b"
                   rf"|\btwo\sthousand(?:\s(?:and\s)?(?P<lo3>{_TWO_DIGIT}|{_U}))?\b", I)
_HOUSE = re.compile(rf"\b(?P<a>{_TWO_DIGIT}|{_U})\s(?P<b>{_TWO_DIGIT}|oh\s(?:{_U}))(?=\s+[A-Z][a-z]+\s+"
                    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Boulevard|Blvd|Way|Place|Pl|Circle|Terrace|Parkway)\b)")


def spoken_dates(text: str) -> list[Replacement]:
    """Ordinals ("the twenty eighth" -> "the 28th") and spoken years ("nineteen fifty two" -> "1952")."""
    out: list[Replacement] = []
    for m in _YEAR.finditer(text):
        if m.group("hi"):
            hi = 19 if m.group("hi").lower() == "nineteen" else 20
            lo = UNITS[m.group("lo1").lower()] if m.group("oh") else _two_digit(m.group("lo2"))
        else:
            hi, lo3 = 20, m.group("lo3")
            lo = 0 if not lo3 else (_two_digit(lo3) if _two_digit(lo3) is not None else UNITS.get(lo3.lower(), 0))
        if lo is not None:
            out.append(Replacement(m.start(), m.end(), f"{hi:02d}{lo:02d}", "spoken_dates"))
    for m in _ORDINAL.finditer(text):
        if m.group("only") and not re.search(r"\b(?:the|of|on|el)\s+$", text[max(0, m.start() - 8):m.start()], I):
            continue   # bare "first"/"second" only when it reads as a day ("the first", "on the second")
        if m.group("tens"):
            n = TENS[m.group("tens").lower()] + ORD_UNITS[m.group("ou").lower()]
        else:
            n = ORD_TEENS.get((m.group("ot") or "").lower()) or ORD_UNITS[(m.group("only") or "").lower()]
        if 1 <= n <= 31:
            suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            out.append(Replacement(m.start(), m.end(), f"{n}{suf}", "spoken_dates"))
    return out


def spoken_address(text: str) -> list[Replacement]:
    """Two-group spoken house numbers before a street name ("fourteen fifty Maple Street" -> "1450 ...")."""
    out: list[Replacement] = []
    for m in _HOUSE.finditer(text):
        a = _two_digit(m.group("a")) if _two_digit(m.group("a")) is not None else UNITS.get(m.group("a").lower())
        b_txt = m.group("b")
        b = UNITS[b_txt.split()[-1].lower()] if b_txt.lower().startswith("oh ") else _two_digit(b_txt)
        if a is not None and b is not None:
            out.append(Replacement(m.start(), m.end(), f"{a}{b:02d}", "spoken_address"))
    return out


_LETTER = r"(?:[A-Za-z]|double\s[A-Za-z])"
_SPELLED = re.compile(rf"(?<![\w'’]){_LETTER}(?:(?:\s*[-,.]\s*|\s+){_LETTER}){{2,}}(?![\w'’])")
_AS_IN = re.compile(r"(?<![\w'’])([A-Za-z])(?:,)?\s+(?:as\s+in|like|for|de|como)\s+[A-Z][a-z]+", )


def spelled_names(text: str) -> list[Replacement]:
    """Letter-by-letter spelling: "D-E-L-A-C-R-O-I-X", "R A M A N", "g, h, e, double r",
    "V as in Victor, A, N" -> one capitalised word."""
    out: list[Replacement] = []
    # "X as in Word" units are rewritten to the bare letter first, then joined with neighbours
    marks = [(m.start(), m.end(), m.group(1)) for m in _AS_IN.finditer(text)]
    scrub = list(text)
    for s, e, ch in marks:
        for i in range(s, e):
            scrub[i] = "\x00"
        scrub[s] = ch
    view = "".join(scrub)
    tokens = re.compile(rf"(?<![\w'’]){_LETTER}(?:\x00*)(?:(?:\s*[-,.]\s*|\s+){_LETTER}(?:\x00*)){{2,}}(?![\w'’])")
    for m in tokens.finditer(view):
        seg = m.group().replace("\x00", "")
        letters = ""
        for t in re.findall(r"double\s[A-Za-z]|[A-Za-z]", seg, I):
            letters += t[-1] * 2 if t.lower().startswith("double") else t
        if len(letters) < 3 or re.fullmatch(r"(?i)[ai]", letters[:1]) and len(letters) < 4 and " " in seg and False:
            continue
        if re.fullmatch(r"(?i)(?:a\s+)?i\s+a", seg):
            continue
        out.append(Replacement(m.start(), m.end(), letters[:1].upper() + letters[1:].lower(), "spelled_names"))
    return out


_DOMAIN = re.compile(r"\s(?:at|arroba)\s+(?P<dom>[a-z0-9]+(?:\s(?:dot|punto)\s[a-z0-9]+)*\s(?:dot|punto)\s"
                     r"(?:com|org|net|edu|gov|us|es|mx|info|io))\b", I)
_LOCAL_TOK = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['’][a-z]+)?")
_LOCAL_STOP = {"is", "it's", "its", "es", "me", "email", "e-mail", "mail", "correo", "to", "my", "mi", "the", "el", "la",
               "address", "dirección", "send", "manda", "mande", "at", "arroba", "that's", "thats", "and", "or",
               "is:", "son", "su", "your", "her", "his", "their", "our", "us", "him", "them", "use", "try", "sorry"}
_LETTER_NAMES = {"be": "b", "ce": "c", "de": "d", "efe": "f", "ge": "g", "hache": "h", "jota": "j", "ka": "k",
                 "ele": "l", "eme": "m", "ene": "n", "pe": "p", "cu": "q", "ere": "r", "erre": "r", "ese": "s",
                 "te": "t", "uve": "v", "ve": "v", "equis": "x", "ye": "y", "zeta": "z"}
_NUM2 = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
         "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "veinte": 20, "treinta": 30, "cuarenta": 40,
         "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}


def _local_value(words: list[str]) -> str:
    out, i = "", 0
    while i < len(words):
        w = words[i].lower()
        if w in ("dot", "punto"):
            out += "."
        elif w in ("underscore",):
            out += "_"
        elif w in ("dash", "guion", "guión"):
            out += "-"
        elif w in ("double", "doble") and i + 1 < len(words):
            nxt = words[i + 1].lower()
            ch = DIGIT_WORDS.get(nxt, _LETTER_NAMES.get(nxt, nxt))
            out += ch * 2
            i += 1
        elif w in _NUM2:
            v = _NUM2[w]
            if i + 2 < len(words) and words[i + 1].lower() == "y" and words[i + 2].lower() in DIGIT_WORDS:
                v += int(DIGIT_WORDS[words[i + 2].lower()])
                i += 2
            elif i + 1 < len(words) and words[i + 1].lower() in DIGIT_WORDS and v >= 20:
                v += int(DIGIT_WORDS[words[i + 1].lower()])
                i += 1
            out += str(v)
        elif w in DIGIT_WORDS and w not in ("o", "una"):
            out += DIGIT_WORDS[w]
        elif w in _LETTER_NAMES:
            out += _LETTER_NAMES[w]
        else:
            out += w
        i += 1
    return out


def spoken_email(text: str) -> list[Replacement]:
    """"j dot rivera at example dot com", "priya dot raman nine one at example dot net",
    "jota ge punto u zeta ce a te e ge u i arroba example punto net" -> address. The local part is
    collected backwards from "at"/"arroba" (max 14 tokens) until a stop word or sentence break."""
    out = []
    for m in _DOMAIN.finditer(text):
        toks = list(_LOCAL_TOK.finditer(text[max(0, m.start() - 160):m.start()]))
        base = max(0, m.start() - 160)
        picked: list[re.Match] = []
        for t in reversed(toks):
            gap_end = picked[-1].start() + base if picked else m.start()
            gap = text[t.end() + base:gap_end]
            if not re.fullmatch(r"[\s,\-.…]*", gap) or "\n" in gap or re.search(r"[.?!]\s", gap) and picked:
                break
            short = lambda w: len(w) <= 2 or w.lower() in _LETTER_NAMES or w.lower() in DIGIT_WORDS
            if "," in gap and not (short(t.group()) and (not picked or short(picked[-1].group()) or True)):
                break   # commas only inside letter-by-letter / digit-by-digit spelling
            if t.group().lower() in _LOCAL_STOP or len(picked) >= 14:
                break
            if t.group()[:1].isupper() and len(t.group()) > 1 and re.search(r"(?:^|\n)\s*$", text[:t.start() + base]):
                break
            picked.append(t)
        if not picked:
            continue
        picked.reverse()
        s0 = picked[0].start() + base
        local = _local_value([t.group() for t in picked])
        dom = re.sub(r"\s(?:dot|punto)\s", ".", m.group("dom"), flags=I)
        if local:
            out.append(Replacement(s0, m.end(), f"{local}@{dom}".lower(), "spoken_email"))
    return out


_FILLER = re.compile(r"(?<![\w'’])(?:um+|uh+|uhm|erm|eh|ehh|mm+|hmm+)(?![\w'’])[,.]?\s*", I)
_REPEAT = re.compile(r"(?<![\w'’])([A-Za-zÀ-ÿ']+)(\s+\1)+(?![\w'’])", I)


def asr_cleanup(text: str) -> list[Replacement]:
    """Remove ASR fillers ("um,", "uh", "ehh") and immediate stutter repeats ("the the" -> "the")."""
    out = [Replacement(m.start(), m.end(), "", "asr_cleanup") for m in _FILLER.finditer(text)]
    for m in _REPEAT.finditer(text):
        if not re.fullmatch(r"(?i)(?:\d+|[a-z]|oh|zero|one|two|three|four|five|six|seven|eight|nine|cero|uno|dos|tres|"
                            r"cuatro|cinco|seis|siete|ocho|nueve|double|no)", m.group(1)):
            out.append(Replacement(m.start(2), m.end(), "", "asr_cleanup"))   # keep the first copy
    return out
