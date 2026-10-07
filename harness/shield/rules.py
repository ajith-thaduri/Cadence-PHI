"""Deterministic PHI detectors (reference implementation for the benchmark).

Every detector returns Prediction objects with native_label = "rule:<rule_id>" and a
canonical label. Rules were developed against the DEV split only (disjoint template
families from test). Design choices follow configs/redaction_policy.yaml:
  - relative dates (next/last/this <weekday>, yesterday, tomorrow, N weeks ago) ARE redacted
  - habitual weekdays ("open Monday through Friday", "on Tuesdays") are NOT
  - years alone, follow-up intervals ("in 3 months") and countries are NOT redacted
  - fractions / vitals that look like dates (1/2 tablet, 120/80) are NOT dates
"""
from __future__ import annotations

import re
from typing import Iterable

from harness.schema import Prediction
from harness.shield.spoken import digit_runs

I = re.IGNORECASE
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
MESES = "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
DIAS = "lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo"
NUMW = ("zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
        "sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand")
ORDW = ("first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|"
        "fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth")
ES_DAYW = ("uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|dieciséis|"
           "diecisiete|dieciocho|diecinueve|veinte|veintiuno|veintidós|veintitrés|veinticuatro|veinticinco|"
           "veintiséis|veintisiete|veintiocho|veintinueve|treinta|treinta y uno")
SPOKEN_YEAR = rf"(?:(?:nineteen|twenty)\s(?:oh\s)?(?:{NUMW})(?:\s(?:{NUMW}))?|two\sthousand(?:\s(?:and\s)?(?:{NUMW}))?)"
YEAR = rf"(?:\d{{4}}|'\d{{2}}|{SPOKEN_YEAR})"
STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
          "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
          "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
          "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
          "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
          "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
          "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
          "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
          "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
          "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC"}
STATE_NAMES = "|".join(sorted((re.escape(s.title().replace(" Of ", " of ")) for s in STATES), key=len, reverse=True))
STATE_ABBR = "|".join(sorted(set(STATES.values())))
# common USPS Publication 28 street suffixes (C1); "Center" is excluded - it names facilities far more often
SUFFIX_FULL = ("Street|Avenue|Road|Drive|Lane|Court|Boulevard|Way|Place|Parkway|Circle|Terrace|Highway|Trail|Loop|Plaza|"
               "Square|Commons|Crossing|Pike|Row|Walk|Path|Alley|Run|Point|Expressway|Freeway|Turnpike|Causeway")
SUFFIX_ABBR = "St|Ave|Rd|Ln|Ct|Blvd|Pl|Pkwy|Cir|Ter|Hwy|Plz|Sq|Xing|Expy|Fwy|Tpke"
NAME_TOKEN = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü'’\-]+"
FUNCTION_WORDS = "by|at|to|in|on|of|for|with|from|and|or|the|was|is|were|via|near|per|than|as"
COMMON_AFTER_TITLE = {"visit", "visits", "office", "appointment", "appt", "said", "says", "told", "wants",
                      "recommended", "note", "notes", "orders", "order", "who", "will", "is", "was", "and",
                      "or", "to", "at", "the", "in", "on", "for", "of", "with", "said", "called", "doctor"}

_ID_VALUE = (r"(?:[A-Za-z]{1,4}[-\s]?)?\d{1,12}(?:[\s\-/]\d{1,12}){0,3}(?![\w])|[A-Z]{1,4}\d{4,14}|\d{6,12}[A-Z]")
_FILLER = (r"(?:\s*(?:is|was|es|:|#|=|-|of|number|num\.?|no\.?|n[uú]mero|uh|um|er|erm|it'?s|its|that'?s|del|de)\s*,?\s*){0,4}"
           r"\s*[\"'“‘(\[]?")      # trailing opener: 'MRN (4471-2290)', 'ID "ABC123"'
# one alphanumeric token, optionally hyphen-segmented, containing a digit: A-99231-X, 3RM7TK2PW48 (CMS MBI), hpv48213q
_ID_TOKEN = r"(?=[A-Za-z\-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+){0,3}(?![\w-])"
KEYWORDS: dict[str, str] = {
    "MRN": r"m\.?r\.?n\.?|m r n|medical record(?: number| no\.?| #)?|med(?:ical)? rec(?:ord)?(?: no\.?| #| number)?|"
           r"record(?: number| no\.?| #)|chart(?: number| no\.?| #)?|historia cl[ií]nica|n[uú]mero de historia(?: cl[ií]nica)?|nhc|cipa?|"
           r"n[uú]mero de paciente|id(?:entificador)? del paciente",
    "MEMBER_ID": r"member(?:ship)?(?: id| number| no\.?| #)|member\s?#|subscriber(?: id| number)|insurance(?: member)?(?: id| number| no\.?)|"
                 r"policy(?: number| no\.?| #)|plan id|medicaid(?: id| number)?|medicare(?: id| number)|health ?plan(?: id| number)|"
                 r"n[uú]mero de (?:miembro|afiliad[oa]|afiliaci[oó]n|seguro|p[oó]liza|la seguridad social)|tarjeta sanitaria|"
                 r"afiliad[oa]|nass|n[uú]m(?:ero)?\.? (?:de )?(?:la )?s(?:eguridad)?\.? ?s(?:ocial)?\.?|\bid\b|member(?=\s\d{6})",
    "ACCOUNT_ID": r"account(?: number| no\.?| #)?|acct\.?(?: number| no\.?| #)?|billing account|cuenta(?: n[uú]mero)?",
    "NPI": r"npi|national provider identifier|n[uú]mero de colegiad[oa]|colegiad[oa](?: n[uú]mero)?|n\.?º?\s?col(?:egiado)?\.?",
    "OTHER_UNIQUE_ID": r"ssn|social security(?: number)?|claim(?: number| no\.?| #)?|authorization(?: number| #)?|auth(?: number| #)|"
                       r"reference(?: number| #)|case(?: number| #)|dni|nie|nif|pasaporte|passport(?: number)?|"
                       r"license(?: number)?|episodio|n[uú]mero de episodio",
}
PHONE_CONTEXT = re.compile(r"(?:phone|cell|mobile|call|callback|reach(?:ed)?|number|tel\.?|teléfono|telefono|tfno|tlf|móvil|movil|celular|llamar|fax|contact|cita previa)\W*(?:\w+\W+){0,6}$", I)
DOB_CONTEXT = re.compile(r"(?:dob|d\.o\.b\.?|d-o-b|date of birth|birth ?date|born(?: on)?|birthday|fecha de nacimiento|f\. ?nac\.?|nacid[oa](?: el)?|nac\.)\W*(?:\w+\W+){0,2}$", I)
DATE_CONTEXT = re.compile(r"(?:on|seen|visit|f/u|follow-?up|since|until|discharged|admitted|appt|appointment|date|dated|from|"
                          r"to|by|scheduled|last|next|el|del|cita|dob|born|and|,|-|:)\W*$", I)


def _p(s: int, e: int, text: str, label: str, rule: str, score: float = 1.0) -> Prediction:
    while s < e and text[s] in " \t\n,;:.":
        s += 1
    while e > s and text[e - 1] in " \t\n,;:":
        e -= 1
    return Prediction(start=s, end=e, text=text[s:e], native_label=f"rule:{rule}", canonical_label=label, score=score)


AMBIGUOUS_ABBR = {"ID", "IN", "OR", "OK", "ME", "HI"}          # state codes that are also English words
CREDENTIAL_ABBR = {"MD", "PA", "DC"}                            # state codes that are also post-nominal credentials
CAPITALISED_FUNCTION_WORDS = {"In", "At", "From", "To", "Near", "Of", "And", "The", "For", "With", "By", "On", "Across",
                              "Throughout", "Outside", "Inside", "Within", "Visiting", "Visited", "Moved", "Lives", "Born"}          # state codes that are also English words


def _state_code_here(text: str, s: int, e: int) -> bool:
    """An ambiguous two-letter code is a STATE only in address position: after "City, " and followed by a ZIP or the
    end of the clause ("Boise, ID 83702", "Boise, ID."), never by "is ..." / ":" ("Remember, ID is CAD 771")."""
    return (re.search(r"[A-Z][a-z]+[\"'”)]?,\s$", text[max(0, s - 30):s]) is not None
            and re.match(r"(?:,?\s\d{5}(?:-\d{4})?\b|\s*[,.;)]|\s*$)", text[e:e + 12]) is not None)


def _after_person_name(text: str, s: int) -> bool:
    """The token before position s is a given name or an initial ("Daniel R. Mercer", "Priya Nanduri"): what follows
    "<Name>, " is a post-nominal credential, not a state."""
    left = text[max(0, s - 30):s]
    return re.search(r"(?:\b[A-Z][a-z]+|\b[A-Z]\.)\s$", left) is not None and \
        re.search(r"\b(?:in|at|from|to|near|of)\s$", left, I) is None


def _ndigits(s: str) -> int:
    return sum(c.isdigit() for c in s)


# ---------------------------------------------------------------------------- identifiers
def keyword_ids(text: str) -> list[Prediction]:
    out = []
    runs = digit_runs(text, 4)
    for label, kw in KEYWORDS.items():
        for m in re.finditer(rf"(?<![\w])(?:{kw})(?![\w])", text, I):
            if m.group() == "ID" and _state_code_here(text, m.start(), m.end()):
                continue      # "Boise, ID 83702": the state code of a "City, ST ZIP" address, not an identifier label
            if label == "MEMBER_ID" and m.group().lower() == "id":
                # bare 'ID' only counts when not part of 'member ID' etc. (handled above) -> generic id
                lab = "OTHER_UNIQUE_ID"
            else:
                lab = label
            rest = text[m.end():m.end() + 60]
            # longest of the grouped-digit form ('4471 2290') and the single-token form ('A-99231-X')
            cands = [x for x in (re.match(_FILLER + rf"({_ID_VALUE})", rest), re.match(_FILLER + rf"({_ID_TOKEN})", rest)) if x]
            v = max(cands, key=lambda x: len(x.group(1).rstrip(" -")), default=None)
            if v and _ndigits(v.group(1)) >= 4:
                s = m.end() + v.start(1)
                val = v.group(1).rstrip(" -")
                out.append(_p(s, s + len(val), text, lab, f"kw_{label.lower()}"))
                continue
            # value a short phrase later ("member ID? Caller: Sure, it's 257302207"): first
            # >=6-digit token within 50 chars, with no other digits in between
            w = re.search(r"(?<![\w])(\d{6,12}|[A-Z]{2,4}\d{6,12})(?![\w])", text[m.end():m.end() + 50])
            if w and not re.search(r"\d", text[m.end():m.end() + w.start(1)]):
                s = m.end() + w.start(1)
                out.append(_p(s, m.end() + w.end(1), text, lab, f"kw_window_{label.lower()}", 0.9))
                continue
            # spoken digits after keyword (ASR)
            for rs, re_, digits in runs:
                if 0 <= rs - m.end() <= 25 and re.fullmatch(_FILLER, text[m.end():rs], I) is not None:
                    out.append(_p(rs, re_, text, lab, f"kw_spoken_{label.lower()}"))
                    break
    # attached forms: mrn:847291, MRN#123, #4829103 after chart words
    for m in re.finditer(r"\b(mrn|acct|npi)[:#](\d{4,12})\b", text, I):
        lab = {"mrn": "MRN", "acct": "ACCOUNT_ID", "npi": "NPI"}[m.group(1).lower()]
        out.append(_p(m.start(), m.end(), text, lab, "attached_id"))
    for m in re.finditer(r"(?<![\w#])#(\d{5,12})\b", text):
        out.append(_p(m.start(), m.end(), text, "OTHER_UNIQUE_ID", "hash_id"))
    # stylised legacy forms
    for m in re.finditer(r"\b(?:TEST-)?(MBR|MRN|NPI|ACCT)-\d{4,12}\b", text, I):
        lab = {"MBR": "MEMBER_ID", "MRN": "MRN", "NPI": "NPI", "ACCT": "ACCOUNT_ID"}[m.group(1).upper()]
        out.append(_p(m.start(), m.end(), text, lab, "stylized_id"))
    # insurance-style alphanumerics without keyword (e.g. XEH394829103)
    for m in re.finditer(r"\b[A-Z]{2,4}\d{8,12}\b", text):
        out.append(_p(m.start(), m.end(), text, "MEMBER_ID", "alnum_id", 0.8))
    # SSN-like / DNI-like
    for m in re.finditer(r"\b\d{3}-\d{2}-\d{4}\b", text):
        out.append(_p(m.start(), m.end(), text, "OTHER_UNIQUE_ID", "ssn"))
    for m in re.finditer(r"\b\d{8}[A-HJ-NP-TV-Z]\b", text):
        out.append(_p(m.start(), m.end(), text, "OTHER_UNIQUE_ID", "dni"))
    return out


def phones(text: str) -> list[Prediction]:
    out = []
    pats = [
        (r"(?<![\d\w])(?:\+?1[\s.-]?)?\(\d{3}\)\s?\d{3}[\s.-]\d{4}(?!\d)", "us_paren"),
        (r"(?<![\d\w])(?:\+?1[\s.-])?\d{3}[.-]\d{3}[.-]\d{4}(?!\d)", "us_sep"),
        (r"(?<![\d\w])\+1\s\d{3}\s\d{3}\s\d{4}(?!\d)", "us_intl"),
        (r"(?<![\d\w])\d{3}\s\d{3}\s\d{4}(?!\d)", "us_space"),
        (r"(?<![\d\w])\+\s?34[\s-]?\d{2,3}(?:[\s.-]?\d{2,3}){2,3}(?!\d)", "es_intl"),
    ]
    for pat, rid in pats:
        for m in re.finditer(pat, text):
            out.append(_p(m.start(), m.end(), text, "PHONE", rid))
    for m in re.finditer(r"(?<![\d\w])\d{10}(?!\d)", text):          # bare 10 digits
        before = text[max(0, m.start() - 30):m.start()]
        lab = "NPI" if re.search(r"npi", before, I) else "PHONE"
        out.append(_p(m.start(), m.end(), text, lab, "bare10", 0.7))
    # extensions belong to the number: '415-555-0107 ext. 12', 'x204'
    for i, p in enumerate(out):
        x = re.match(r"\s?,?\s?(?:ext\.?|extension|x)\s?\d{1,6}\b", text[p.end:], I)
        if x:
            out[i] = _p(p.start, p.end + x.end(), text, p.canonical_label, p.native_label.split(":", 1)[1], p.score)
    for m in re.finditer(r"(?<![\d\w])\d{3}[\s.]\d{2}[\s.]\d{2}[\s.]\d{2}(?!\d)|(?<![\d\w])[5-9]\d{2}[\s.]\d{3}[\s.]\d{3}(?!\d)|(?<![\d\w])[6-9]\d{8}(?!\d)", text):
        if PHONE_CONTEXT.search(text[max(0, m.start() - 40):m.start()]):
            out.append(_p(m.start(), m.end(), text, "PHONE", "es_local"))
    return out


def spoken_numbers(text: str) -> list[Prediction]:
    """Spoken digit runs not already claimed by a keyword (keyword_ids handles those)."""
    out = []
    for s, e, d in digit_runs(text, 5):
        before = text[max(0, s - 40):s]
        n = len(d)
        if re.search(r"npi", before, I):
            lab = "NPI"
        elif n in (10, 11) or (n in (7, 9) and PHONE_CONTEXT.search(before)):
            lab = "PHONE"
        elif n == 5 and re.search(r"zip|postal", before, I):
            lab = "ZIP"
        else:
            lab = "OTHER_UNIQUE_ID"
        out.append(_p(s, e, text, lab, f"spoken_{n}"))
    return out


def emails_urls(text: str) -> list[Prediction]:
    out = []
    for m in re.finditer(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        out.append(_p(m.start(), m.end(), text, "EMAIL", "email"))
    for m in re.finditer(r"\b[a-z0-9]+(?:\s(?:dot|punto|underscore|guion bajo)\s[a-z0-9]+){0,3}\s(?:at|arroba)\s[a-z0-9]+(?:\s(?:dot|punto)\s[a-z0-9]+)+\b", text, I):
        out.append(_p(m.start(), m.end(), text, "EMAIL", "email_spoken"))
    for m in re.finditer(r"\b(?:https?://|www\.)[^\s<>\"]+", text, I):
        e = m.end()
        while e > m.start() and text[e - 1] in ".,;:)]":
            e -= 1
        out.append(_p(m.start(), e, text, "URL", "url"))
    return out


_ZIP_BRIDGE = r"(?:(?:um|uh|er),?\s)?[\"'“(]?"


def zips(text: str) -> list[Prediction]:
    out = []
    for m in re.finditer(rf"(?:\b(?:{STATE_ABBR})|\b(?:{STATE_NAMES})),?\s(\d{{5}}(?:-\d{{4}})?)\b", text):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_state"))
    for m in re.finditer(rf"\b(?:{STATE_NAMES.lower()}),?\s(\d{{5}}(?:-\d{{4}})?)\b", text):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_state_lower"))
    for m in re.finditer(r"(?:º\s?(?:[a-z]\b|izq\.?|dcha\.?)|\b(?:calle|avenida|plaza|paseo)\s[^\n]{3,40}?),?\s(\d{5})\b", text, I):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_after_es_address"))
    # lowercase / wrapped / disfluent forms after ', <state abbr>': 'fresno, ca 93701', 'Fresno, CA (93701-1234)'
    for m in re.finditer(rf"(?:,\s?(?:{STATE_ABBR})|\b(?:{STATE_NAMES})),?\s{_ZIP_BRIDGE}(\d{{5}}(?:-\d{{4}})?)(?![\d-])", text, I):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_state_wrapped"))
    for m in re.finditer(rf"(?:zip(?: code)?|c[oó]digo postal|c\.p\.|\bcp\b|\bE-)\W{{0,3}}(?:is\s|es\s)?{_ZIP_BRIDGE}(\d{{5}}(?:-\d{{4}})?)(?![\d-])", text, I):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_kw"))
    for m in re.finditer(r"(?:,|\b\d{1,2}º\s?\w+\.?,?)\s(\d{5})\s(?=[A-ZÁÉÍÓÚ][a-záéíóúñ]+)", text):
        out.append(_p(m.start(1), m.end(1), text, "ZIP", "zip_es_city"))
    return out


# ---------------------------------------------------------------------------------- dates
def _plausible_md(a: int, b: int) -> bool:
    return (1 <= a <= 12 and 1 <= b <= 31) or (1 <= b <= 12 and 1 <= a <= 31)


def dates(text: str) -> list[Prediction]:
    out: list[Prediction] = []

    def add(s, e, rid, score=1.0):
        before = text[max(0, s - 40):s]
        lab = "DOB" if DOB_CONTEXT.search(before) else "DATE"
        out.append(_p(s, e, text, lab, rid, score))

    for m in re.finditer(r"(?<![\d/.\-])(\d{1,2})([/\-.])(\d{1,2})\2\s?(\d{4}|\d{2})(?![\d/]|\.\d)", text):
        if _plausible_md(int(m.group(1)), int(m.group(3))):
            add(m.start(), m.end(), "numeric_full")
    for m in re.finditer(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", text):
        add(m.start(), m.end(), "iso")
    for m in re.finditer(r"(?<![\d/.\-])(\d{1,2})/(\d{1,2})(?![\d/])", text):
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 31 and DATE_CONTEXT.search(text[max(0, m.start() - 25):m.start()]):
            scale = re.search(r"(?:pain|dolor|eva|escala|fuerza|strength|score|puntuaci[oó]n|grade|grado|balance)\W*(?:\w+\W+){0,2}$", text[max(0, m.start() - 30):m.start()], I)
            if not scale and not re.match(r"\s*(?:tab|tablet|pill|cup|dose|of|mg|ml|strength)", text[m.end():m.end() + 12], I):
                add(m.start(), m.end(), "numeric_md", 0.8)
    mon = rf"(?:{MONTHS})\.?"
    pats = [
        (rf"\b{mon}\s\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s{YEAR})?\b", "month_day"),
        (rf"\b{mon}\s(?:the\s)?(?:{ORDW})(?:[\s-](?:{ORDW}))?(?:,?\s{YEAR})?\b", "month_spoken_day"),
        (rf"\b(?:{mon}\s)?(?:twenty|thirty)[\s-](?:{ORDW})(?:,?\s{YEAR})?\b", "spoken_ordinal_compound"),
        (rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s(?:of\s)?{mon}(?:,?\s{YEAR})?\b", "day_month"),
        (rf"\b(?:the\s)?(?:{ORDW})\sof\s{mon}(?:,?\s{YEAR})?\b", "spoken_day_of_month"),
        (rf"\b(?:{NUMW})(?:[\s-](?:{NUMW}))?\s{mon}\b", "numword_month"),
        (rf"\b{mon},?\s(?:\d{{4}}|{SPOKEN_YEAR})\b", "month_year"),
        (rf"\b\d{{1,2}}\sde\s(?:{MESES})(?:\s(?:de|del)\s\d{{4}})?\b", "es_dia_mes"),
        (rf"\b(?:{ES_DAYW})\sde\s(?:{MESES})(?:\s(?:de|del)\s\d{{4}})?\b", "es_dia_palabra"),
        (rf"\b(?:{MESES})\s(?:de\s)?\d{{4}}\b", "es_mes_ano"),
        (rf"\b\d{{1,2}}[-/\s](?:{MESES})[-/\s]\d{{2,4}}\b", "es_dia_mes_ano_sep"),
    ]
    for pat, rid in pats:
        for m in re.finditer(pat, text, I):
            g = m.group()
            if rid in ("month_day", "month_year", "month_spoken_day") and g.lower().startswith("may") and not re.search(r"\d|first|second|third|th\b", g[3:], I):
                continue
            if rid == "spoken_ordinal_compound" and not re.match(rf"(?:{mon})", g, I) and not re.search(MONTHS, text[max(0, m.start() - 15):m.start()], I):
                continue
            add(m.start(), m.end(), rid)
    # relative dates (policy: redacted)
    rel = [
        (rf"\b(?:next|last|this|coming|past)\s(?:{WEEKDAYS}|week|weekend|month)\b", "relative_weekday"),
        (r"\b(?:yesterday|tomorrow)\b", "yesterday_tomorrow"),
        (rf"\b(?:\d+|a|a couple of|a few|several|{NUMW})(?:\s(?:{NUMW}))?\s(?:days?|weeks?)\sago\b", "n_ago"),
        (rf"\b(?:el\s)?(?:pr[oó]ximo|pasado)\s(?:{DIAS})\b|\b(?:{DIAS})\s(?:pr[oó]ximo|pasado)\b", "es_relative"),
        (r"\bayer\b|\banteayer\b", "es_ayer"),
        (rf"\bhace\s(?:\d+|un[oa]?|dos|tres|cuatro|cinco)\s(?:d[ií]as|semanas?)\b", "es_hace"),
    ]
    for pat, rid in rel:
        for m in re.finditer(pat, text, I):
            add(m.start(), m.end(), rid, 0.9)
    # bare weekday in an appointment-like frame, not habitual ("Mondays", "Monday through Friday")
    for m in re.finditer(rf"\b(?:{WEEKDAYS})\b(?!s)", text, I):
        before = text[max(0, m.start() - 20):m.start()].lower()
        after = text[m.end():m.end() + 14].lower()
        if re.search(r"(?:through|thru|to|-|–)\s*$", before) or re.match(r"\s*(?:through|thru|to|-|–)\s", after):
            continue
        if re.search(r"(?:next|last|this|coming|past)\s*$", before):
            continue  # already covered by relative rule
        if re.search(r"(?:\bon|\bis|\bby|\buntil|\bsince|\bfor|\bscheduled|\bappointment)\s*$", before):
            add(m.start(), m.end(), "bare_weekday", 0.7)
    return out


# ---------------------------------------------------------------------------- addresses
def addresses(text: str) -> list[Prediction]:
    out = []
    unit = r"(?:\s(?:NW|NE|SW|SE|N\.?W\.?|N\.?E\.?|S\.?W\.?|S\.?E\.?)\b)?(?:,?\s(?:Apt\.?|Apartment|Unit|Suite|Ste\.?|#)\s?[\w-]+)?"
    word = rf"(?!(?i:{FUNCTION_WORDS})\b)[A-Za-z][A-Za-z'.-]*"
    pats = [
        (rf"\b\d{{1,6}}\s(?:[NSEWnsew]\.?\s)?(?:{word}\s){{1,3}}(?:{SUFFIX_FULL})\b{unit}", "street_number_full", I),
        (rf"\b\d{{1,6}}\s(?:[NSEW]\.?\s)?(?:{word}\s){{1,3}}(?:{SUFFIX_ABBR}|Dr)\.?(?=[\s,.;]|$){unit}", "street_number_abbr", 0),
        # lowercase abbreviations (typed chat): 'dr', 'ct', 'pl' excluded - they collide with 'by dr', counts, plurals
        (rf"\b\d{{1,6}}\s(?:[nsew]\.?\s)?(?:(?!(?:{FUNCTION_WORDS})\b)[a-z][a-z'.-]*\s){{1,3}}(?:st|ave|rd|ln|blvd|pkwy|hwy)\.?(?=[\s,.;]|$)(?i:{unit})", "street_number_abbr_lower", 0),
        (rf"\b(?:(?:{NUMW})\s){{1,6}}(?:[a-z]+\s){{1,3}}(?:street|avenue|road|drive|lane|court|boulevard|way|place|parkway)\b", "street_spoken", I),
        (rf"\b(?:{NAME_TOKEN}\s){{1,2}}(?:{SUFFIX_FULL})\b", "street_name_full", 0),
        (rf"\b{NAME_TOKEN}\s(?:{SUFFIX_ABBR})\.?(?=[\s,.;]|$)", "street_name_abbr", 0),
        (rf"\b(?:on|near|at|off|in|en|por|y|and|of)\s((?:[a-z]+\s){{1,2}}(?:street|avenue|boulevard|road|lane|drive|court|parkway|way|place))\b", "street_name_lower", 0),
        (r"\bP\.?\s?O\.?\sBox\s\d+\b", "po_box", I),
        (r"\b(?:C/|c/|Calle|calle|Avda\.?|Avenida|avenida|Av\.|Plaza|plaza|Pza\.?|Paseo|paseo|Camino|Carretera|Ronda|Travesía|Glorieta)\s"
         r"(?:(?:de|del|la|los|las|el)\s)*[A-ZÁÉÍÓÚÑa-záéíóúñ][\wáéíóúñ]+(?:\s(?:(?:de|del|la|los|las|el)\s)*[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+){0,3}"
         r"(?:,?\s?(?:n\.?º|nº|número)?\s?\d{1,4}(?:[-–]\d{1,4})?)?(?:,?\s?\d{1,2}º\s?(?:[A-Z]\b|izq\.?|dcha\.?|izquierda|derecha))?", "es_street", 0),
    ]
    for pat, rid, flags in pats:
        for m in re.finditer(pat, text, flags):
            g = 1 if (m.lastindex and rid == "street_name_lower") else 0
            out.append(_p(m.start(g), m.end(g), text, "ADDRESS", rid, 0.9))
    # labelled fields: "Domicilio: ...", "Address: ...", "Localidad/ Provincia: Madrid"
    for m in re.finditer(r"(?<!ip )(?<!mac )(?<!email )(?<!e-mail )(?<!web )(?<!internet )(?<!hardware )(?:domicilio|direcci[oó]n|home address|address)\s?:\s?([^\n|]{4,70}?)(?=\s?\.\s|\s?\.$|\s\||\n|$)", text, I):
        out.append(_p(m.start(1), m.end(1), text, "ADDRESS", "field_address", 0.9))
    for m in re.finditer(r"(?:localidad|provincia|ciudad|municipio|poblaci[oó]n|city)(?:\s?/\s?provincia)?\s?:\s?([A-ZÁÉÍÓÚ][\wáéíóúñ]+(?:\s(?:de\s|del\s|la\s)?[A-ZÁÉÍÓÚ][\wáéíóúñ]+){0,2})", text, I):
        out.append(_p(m.start(1), m.end(1), text, "CITY", "field_city", 0.9))
    # City, ST / City, State
    city_tok = rf"(?!(?:{SUFFIX_FULL}|{SUFFIX_ABBR})\b){NAME_TOKEN}"     # a street suffix never starts/ends a city
    for m in re.finditer(rf"\b((?:{city_tok}\s)?{city_tok})[\"'”)]?,\s(?:({STATE_ABBR})\b|({STATE_NAMES})\b)", text):
        if m.group(2) in AMBIGUOUS_ABBR and not _state_code_here(text, m.start(2), m.end(2)):
            continue                                   # "Remember, ID is CAD ...": a label, not Idaho
        if m.group(2) in CREDENTIAL_ABBR and (_after_person_name(text, m.start(1))
                                             or not _state_code_here(text, m.start(2), m.end(2))):
            continue                                   # "Daniel R. Mercer, MD documented": a credential, not Maryland
        out.append(_p(m.start(1), m.end(1), text, "CITY", "city_state", 0.9))
        g = 2 if m.group(2) else 3
        out.append(_p(m.start(g), m.end(g), text, "STATE", "state_after_city"))
    for m in re.finditer(rf"\b(?:{STATE_NAMES})\b", text):
        if re.match(rf",\s(?:{STATE_ABBR})\b", text[m.end():m.end() + 5]):
            continue                                   # "<state name>, DC": the name is the city (city_state)
        if re.match(rf"\s(?:{SUFFIX_FULL}|{SUFFIX_ABBR})\b", text[m.end():m.end() + 12]):
            continue                                   # "Pennsylvania Avenue": part of a street name
        prev = re.search(r"\b([A-Z][a-z]+)\s$", text[max(0, m.start() - 20):m.start()])
        if prev and prev.group(1) not in CAPITALISED_FUNCTION_WORDS:
            continue                                   # "Darnell Washington": a surname after a given name
        if re.match(r"\s[A-Z][a-z]+\b", text[m.end():m.end() + 20]) and not re.match(r",|\s\d", text[m.end():m.end() + 2]):
            continue                                   # "Georgia Smith": a given name before a surname
        out.append(_p(m.start(), m.end(), text, "STATE", "state_name", 0.8))
    # "<state> (vehicle) registration <plate>": the plate registered in that state
    for m in re.finditer(rf"\b(?:{STATE_NAMES}|{STATE_ABBR})\s(?:vehicle\s)?registration(?:\s(?:number|no\.?|#|plate))?[:#]?\s"
                         r"(?:(?:um|uh|er),?\s)?[\"'(]?"
                         r"((?=[A-Z0-9 -]*[A-Z])(?=[A-Z0-9 -]*\d)[A-Z0-9]{2,4}[- ]?[A-Z0-9]{2,5})\b", text, I):
        out.append(_p(m.start(1), m.end(1), text, "VEHICLE_ID", "state_vehicle_registration", 0.9))
    # an upper-case state code naming the issuing state of a plate / licence ("DC plate", "CA driver's license")
    for m in re.finditer(rf"\b({STATE_ABBR})\b(?=\s(?:license|licence|driver'?s|driving|plate|tag|registration|dl)\b)", text):
        if m.group(1) not in AMBIGUOUS_ABBR:
            out.append(_p(m.start(1), m.end(1), text, "STATE", "state_code_issuer", 0.9))
    for m in re.finditer(r"\b\d{5}\s([A-ZÁÉÍÓÚ][a-záéíóúñ]+(?:\s(?:de\s)?[A-ZÁÉÍÓÚ][a-záéíóúñ]+)?)", text):
        out.append(_p(m.start(1), m.end(1), text, "CITY", "es_city_after_zip", 0.8))
    return out


# ---------------------------------------------------------------------------- titled names
def titled_names(text: str) -> list[Prediction]:
    out = []
    for m in re.finditer(rf"\b(?:Dr|Dra|Doctor|Doctora|Prof)\.?\s({NAME_TOKEN}(?:\s{NAME_TOKEN}){{0,2}})", text):
        out.append(_p(m.start(), m.end(1), text, "PROVIDER_NAME", "title_provider"))
    for m in re.finditer(r"\b(?:dr|dra|doctor|doctora)\.?\s([a-záéíóúñ]{3,}(?:\s[a-záéíóúñ]{3,})?)", text):
        first = m.group(1).split()[0]
        if first not in COMMON_AFTER_TITLE:
            words = m.group(1).split()
            end = m.start(1) + len(words[0]) if len(words) > 1 and words[1] in COMMON_AFTER_TITLE else m.end(1)
            out.append(_p(m.start(), end, text, "PROVIDER_NAME", "title_provider_lower", 0.8))
    for m in re.finditer(rf"\b(?:Mr|Mrs|Ms|Miss|Mister|Sr|Sra|Srta|Don|Doña)\.?\s({NAME_TOKEN}(?:\s{NAME_TOKEN}){{0,2}})", text):
        out.append(_p(m.start(), m.end(1), text, "NAME", "title_person"))
    for m in re.finditer(r"\b(?:mr|mrs|ms|miss|mister|sr|sra|srta)\.?\s([a-záéíóúñ]{2,}(?:\s[a-záéíóúñ]{3,})?)", text):
        if m.group(1).split()[0] not in COMMON_AFTER_TITLE:
            words = m.group(1).split()
            end = m.start(1) + len(words[0]) if len(words) > 1 and words[1] in COMMON_AFTER_TITLE else m.end(1)
            out.append(_p(m.start(), end, text, "NAME", "title_person_lower", 0.8))
    for m in re.finditer(rf"\b({NAME_TOKEN}\s(?:[A-Z]\.\s)?{NAME_TOKEN})[)\"”']?,?\s(MD|M\.D\.|DO|NP|PA-C|RN|LCSW|DPT|PharmD|DNP|PhD)\b", text):
        if m.group(2) in CREDENTIAL_ABBR and re.search(r"\b(?:in|to|from|at|near|of)\s$", text[max(0, m.start() - 8):m.start()], I):
            continue      # "moved to Silver Spring, MD": a place followed by its state code
        out.append(_p(m.start(1), m.end(1), text, "PROVIDER_NAME", "credential_provider"))
    return out


ALL_RULES = [keyword_ids, phones, spoken_numbers, emails_urls, zips, dates, addresses, titled_names]


def detect(text: str, rules: Iterable = ALL_RULES) -> list[Prediction]:
    out: list[Prediction] = []
    for r in rules:
        out.extend(p for p in r(text) if p.end > p.start)
    return out
