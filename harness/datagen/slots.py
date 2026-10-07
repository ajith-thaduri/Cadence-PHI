"""Slot registry (v2): how each template placeholder renders and how it is annotated.

A slot returns the surface string; the registry gives its canonical gold label (None =
not PHI), entity difficulty and tags. `ignore=True` slots become ignore spans (regions
where predictions are neither TP nor FP, e.g. ages - no canonical AGE label - and
country names, which the default redaction policy does not treat as identifiers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from harness.datagen.values import (
    MONTHS_ABBR, MONTHS_EN, MONTHS_ES, WEEKDAYS_EN, WEEKDAYS_ES, Persona, es_day, num_words,
    ordinal_suffix, ordinal_words, spoken_digits, year_words,
)


@dataclass
class Slot:
    label: Optional[str]
    render: Callable[[Persona], str]
    difficulty: str = "easy"
    tags: list[str] = field(default_factory=list)
    ignore: bool = False


def _long(d) -> str:
    return f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"


def _es(d) -> str:
    return f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}"


def _street(p: Persona, abbr=False, number=True) -> str:
    name, suf, ab = p.street
    s = f"{name} {ab if abbr else suf}"
    return f"{p.street_no} {s}" if number else s


def _street_spoken(p: Persona) -> str:
    n = p.street_no
    if n >= 1000 and n % 100 >= 10:
        num = f"{num_words(n // 100)} {num_words(n % 100)}"
    else:
        num = num_words(n) if n < 1000 else spoken_digits(str(n))
    return f"{num} {p.street[0].lower()} {p.street[1].lower()}"


def _phone(p: Persona, fmt: str) -> str:
    a, l = p.area, p.line
    return {"paren": f"({a}) 555-{l}", "dash": f"{a}-555-{l}", "dot": f"{a}.555.{l}",
            "digits": f"{a}555{l}", "intl": f"+1 {a} 555 {l}"}[fmt]


def _rel_weekday(p: Persona, word: str) -> str:
    return f"{word} {WEEKDAYS_EN[p.rng.randrange(7)]}"


S = Slot
SLOTS: dict[str, Slot] = {
    # ---- people ------------------------------------------------------------------------
    "patient": S("NAME", lambda p: p.patient),
    "patient_first": S("NAME", lambda p: p.patient_first, "medium", ["first_name_only", "contextual"]),
    "patient_last": S("NAME", lambda p: p.patient_last, "medium", ["last_name_only", "contextual"]),
    "patient_nick": S("NAME", lambda p: p.nickname, "hard", ["nickname", "first_name_only", "contextual"]),
    "patient_titled": S("NAME", lambda p: f"{'Mrs.' if p.patient_gender == 'f' else 'Mr.'} {p.patient_last}", "medium", ["last_name_only"]),
    "patient_titled_es": S("NAME", lambda p: f"{'Sra.' if p.patient_gender == 'f' else 'Sr.'} {p.patient_last.split()[0]}", "medium", ["last_name_only"]),
    "patient_initial": S("NAME", lambda p: f"{p.patient_first[0]}. {p.patient_last}", "medium", ["partial_name"]),
    "patient_lastfirst": S("NAME", lambda p: f"{p.patient_last.upper()}, {p.patient_first.upper()}", "medium", ["structured"]),
    "patient_accent": S("NAME", lambda p: _accent(p.patient), "hard", ["asr", "accent_variation"]),
    "relative": S("RELATIVE_NAME", lambda p: p.relative_first, "medium", ["relative", "contextual", "first_name_only"]),
    "relative_full": S("RELATIVE_NAME", lambda p: f"{p.relative_first} {p.relative_last.split()[0]}", "easy", ["relative"]),
    "provider": S("PROVIDER_NAME", lambda p: f"Dr. {p.provider_first} {p.provider_last}", "easy", ["titled"]),
    "provider_short": S("PROVIDER_NAME", lambda p: f"Dr. {p.provider_last}", "easy", ["titled"]),
    "provider_short_nodot": S("PROVIDER_NAME", lambda p: f"Dr {p.provider_last}", "medium", ["titled", "asr"]),
    "provider_es": S("PROVIDER_NAME", lambda p: f"{p.title_es} {p.provider_last}", "easy", ["titled"]),
    "provider_bare": S("PROVIDER_NAME", lambda p: p.provider_last, "hard", ["contextual", "last_name_only", "provider_facility_ambiguity"]),
    "provider_untitled": S("PROVIDER_NAME", lambda p: f"{p.provider_first} {p.provider_last}", "medium", ["contextual"]),
    "provider2": S("PROVIDER_NAME", lambda p: f"Dr. {p.provider2_first} {p.provider2_last}", "easy", ["titled", "out_of_registry"]),
    "provider2_untitled": S("PROVIDER_NAME", lambda p: f"{p.provider2_first} {p.provider2_last}", "medium", ["out_of_registry"]),
    "provider_accent": S("PROVIDER_NAME", lambda p: f"doctor {_accent(p.provider_last).lower()}", "hard", ["asr", "accent_variation", "titled"]),
    "care_team": S("CARE_TEAM_MEMBER", lambda p: p.care_team, "medium", ["contextual"]),
    "care_team_first": S("CARE_TEAM_MEMBER", lambda p: p.care_team.split()[0], "hard", ["contextual", "first_name_only"]),
    # ---- organisations / places ----------------------------------------------------------
    "facility": S("FACILITY_NAME", lambda p: p.facility),
    "facility_short": S("FACILITY_NAME", lambda p: p.facility_short, "hard", ["contextual", "provider_facility_ambiguity"]),
    "facility2": S("FACILITY_NAME", lambda p: p.facility2, "easy", ["out_of_registry"]),
    "employer": S("EMPLOYER", lambda p: p.employer, "medium", ["employer", "contextual"]),
    "org": S("ORGANIZATION", lambda p: p.org, "medium", ["contextual"]),
    "language": S("LANGUAGE", lambda p: p.language_en, "medium", ["contextual"]),
    "language_es": S("LANGUAGE", lambda p: p.language_es, "medium", ["contextual"]),
    "landmark": S("GEOGRAPHIC_CONTEXT", lambda p: p.landmark, "hard", ["contextual"]),
    "address": S("ADDRESS", lambda p: _street(p) + (f" {p.unit}" if p.unit else "")),
    "address_abbr": S("ADDRESS", lambda p: _street(p, abbr=True), "medium"),
    "street_only": S("ADDRESS", lambda p: _street(p, abbr=True, number=False), "hard", ["partial_address"]),
    "street_full_only": S("ADDRESS", lambda p: _street(p, number=False), "hard", ["partial_address"]),
    "street2_only": S("ADDRESS", lambda p: f"{p.street2[0]} {p.street2[1]}", "hard", ["partial_address", "intersection"]),
    "address_spoken": S("ADDRESS", _street_spoken, "hard", ["asr", "spoken_number"]),
    "address_es": S("ADDRESS", lambda p: f"{p.street_es} {p.street_no % 120 + 1}, {p.rng.randint(1, 6)}º {p.rng.choice(['A', 'B', 'izq.', 'dcha.'])}"),
    "city": S("CITY", lambda p: p.city[0], "medium"),
    "city_es": S("CITY", lambda p: p.city_es[0], "medium"),
    "state": S("STATE", lambda p: p.city[1], "medium"),
    "state_abbr": S("STATE", lambda p: p.city[2], "medium"),
    "zip": S("ZIP", lambda p: p.zip, "medium"),
    "zip_es": S("ZIP", lambda p: p.city_es[1], "medium"),
    "zip_spoken": S("ZIP", lambda p: spoken_digits(p.zip), "hard", ["asr", "spoken_number"]),
    # ---- contact -------------------------------------------------------------------------
    "phone": S("PHONE", lambda p: _phone(p, "paren")),
    "phone_dashed": S("PHONE", lambda p: _phone(p, "dash")),
    "phone_dots": S("PHONE", lambda p: _phone(p, "dot"), "medium"),
    "phone_intl": S("PHONE", lambda p: _phone(p, "intl"), "medium"),
    "phone_digits": S("PHONE", lambda p: _phone(p, "digits"), "hard", ["unformatted_number"]),
    "phone_spoken": S("PHONE", lambda p: spoken_digits(p.phone_digits), "hard", ["asr", "spoken_number"]),
    "phone_spoken_grouped": S("PHONE", lambda p: spoken_digits(f"{p.area} 555 {p.line}", group_sep=", "), "hard", ["asr", "spoken_number"]),
    "phone_es": S("PHONE", lambda p: f"+34 {p.phone_es}", "medium"),
    "phone_es_local": S("PHONE", lambda p: p.phone_es, "medium"),
    "phone_spoken_es": S("PHONE", lambda p: spoken_digits(p.phone_es, "es"), "hard", ["asr", "spoken_number"]),
    "email": S("EMAIL", lambda p: p.email),
    "email_spoken": S("EMAIL", lambda p: p.email.replace(".", " dot ").replace("@", " at "), "hard", ["asr"]),
    "email_spoken_es": S("EMAIL", lambda p: p.email.replace(".", " punto ").replace("@", " arroba "), "hard", ["asr"]),
    "url": S("URL", lambda p: f"https://portal.{p.domain}/members/{p.member_id}", "medium"),
    # ---- identifiers (natural formats first; stylised kept as a minority) -----------------
    "member_id": S("MEMBER_ID", lambda p: p.member_id, "medium", ["natural_format"]),
    "member_alnum": S("MEMBER_ID", lambda p: p.member_alnum, "medium", ["natural_format", "insurance_id"]),
    "member_spoken": S("MEMBER_ID", lambda p: spoken_digits(p.member_id), "hard", ["asr", "spoken_number"]),
    "member_stylized": S("MEMBER_ID", lambda p: f"TEST-MBR-{p.member_id[-6:]}", "easy", ["stylized_format"]),
    "mrn": S("MRN", lambda p: p.mrn, "medium", ["natural_format"]),
    "mrn_colon": S("MRN", lambda p: f"mrn:{p.mrn}", "hard", ["structured", "natural_format"]),
    "mrn_hash": S("MRN", lambda p: f"#{p.mrn}", "medium", ["natural_format"]),
    "mrn_spoken": S("MRN", lambda p: spoken_digits(p.mrn), "hard", ["asr", "spoken_number"]),
    "mrn_stylized": S("MRN", lambda p: f"MRN-{p.mrn[-6:]}", "easy", ["stylized_format"]),
    "npi": S("NPI", lambda p: p.npi, "medium", ["natural_format"]),
    "npi_spoken": S("NPI", lambda p: spoken_digits(p.npi), "hard", ["asr", "spoken_number"]),
    "account": S("ACCOUNT_ID", lambda p: p.account, "medium", ["natural_format"]),
    "account_dashed": S("ACCOUNT_ID", lambda p: f"{p.account[:4]}-{p.account[4:]}", "medium", ["natural_format"]),
    "claim": S("OTHER_UNIQUE_ID", lambda p: p.claim_id, "medium", ["natural_format"]),
    "dni": S("OTHER_UNIQUE_ID", lambda p: p.dni, "medium", ["natural_format"]),
    "nhc_es": S("MRN", lambda p: p.mrn, "medium", ["natural_format"]),
    # ---- dates of birth ------------------------------------------------------------------
    "dob": S("DOB", lambda p: _long(p.dob)),
    "dob_slash": S("DOB", lambda p: p.dob.strftime("%m/%d/%Y")),
    "dob_short": S("DOB", lambda p: p.dob.strftime("%m-%d-%y"), "hard", ["malformed_date"]),
    "dob_ordinal": S("DOB", lambda p: f"{MONTHS_EN[p.dob.month - 1]} {p.dob.day}{ordinal_suffix(p.dob.day)} {p.dob.year}", "medium"),
    "dob_spoken": S("DOB", lambda p: f"{MONTHS_EN[p.dob.month - 1].lower()} {ordinal_words(p.dob.day)} {year_words(p.dob.year)}", "hard", ["asr", "spoken_number"]),
    "dob_es": S("DOB", lambda p: _es(p.dob)),
    "dob_es_slash": S("DOB", lambda p: p.dob.strftime("%d/%m/%Y")),
    # ---- dates ----------------------------------------------------------------------------
    "date": S("DATE", lambda p: _long(p.visit)),
    "date_slash": S("DATE", lambda p: p.visit.strftime("%m/%d/%Y")),
    "date_short": S("DATE", lambda p: p.visit.strftime("%m/%d/%y"), "medium"),
    "date_md": S("DATE", lambda p: f"{p.visit.month}/{p.visit.day}", "medium", ["partial_date"]),
    "date_month_day": S("DATE", lambda p: f"{MONTHS_EN[p.visit.month - 1]} {p.visit.day}", "medium", ["partial_date"]),
    "date_abbr_ord": S("DATE", lambda p: f"{MONTHS_ABBR[p.visit.month - 1]} {p.visit.day}{ordinal_suffix(p.visit.day)}", "medium", ["partial_date"]),
    "date_day_month": S("DATE", lambda p: f"{num_words(p.visit.day) if p.visit.day < 20 else p.visit.day} {MONTHS_EN[p.visit.month - 1]}", "hard", ["partial_date"]),
    "date_spoken": S("DATE", lambda p: f"{MONTHS_EN[p.visit.month - 1].lower()} {ordinal_words(p.visit.day)}", "hard", ["asr", "spoken_number", "partial_date"]),
    "date_spoken_year": S("DATE", lambda p: f"{MONTHS_EN[p.visit.month - 1]} {ordinal_words(p.visit.day)} {year_words(p.visit.year)}", "hard", ["spoken_number"]),
    "date_malformed": S("DATE", lambda p: f"{p.visit.month}/{p.visit.day}/ {p.visit.year % 100}", "hard", ["malformed_date"]),
    "date_es": S("DATE", lambda p: _es(p.visit)),
    "date_es_short": S("DATE", lambda p: f"{p.visit.day} de {MONTHS_ES[p.visit.month - 1]}", "medium", ["partial_date"]),
    "date_es_spoken": S("DATE", lambda p: f"{es_day(p.visit.day)} de {MONTHS_ES[p.visit.month - 1]}", "hard", ["spoken_number", "partial_date"]),
    "rel_next": S("DATE", lambda p: _rel_weekday(p, "next"), "hard", ["relative_date"]),
    "rel_last": S("DATE", lambda p: _rel_weekday(p, "last"), "hard", ["relative_date"]),
    "rel_this": S("DATE", lambda p: _rel_weekday(p, "this"), "hard", ["relative_date"]),
    "weekday": S("DATE", lambda p: WEEKDAYS_EN[p.rng.randrange(7)], "hard", ["relative_date", "bare_weekday"]),
    "yesterday": S("DATE", lambda p: "yesterday", "hard", ["relative_date"]),
    "tomorrow": S("DATE", lambda p: "tomorrow", "hard", ["relative_date"]),
    "weeks_ago": S("DATE", lambda p: f"{num_words(p.rng.randint(2, 5))} weeks ago", "hard", ["relative_date"]),
    "rel_es": S("DATE", lambda p: f"el próximo {WEEKDAYS_ES[p.rng.randrange(7)]}", "hard", ["relative_date"]),
    "ayer": S("DATE", lambda p: "ayer", "hard", ["relative_date"]),
    # ---- plain filler (not PHI) ----------------------------------------------------------
    "relation": S(None, lambda p: p.relation),
    "relation_es": S(None, lambda p: p.relation_es),
    "weekday_generic": S(None, lambda p: WEEKDAYS_EN[p.rng.randrange(5)]),
    # ---- ignore regions (not scored as PHI either way) -------------------------------------
    "age": S(None, lambda p: f"{p.age}-year-old", ignore=True),
    "age_plain": S(None, lambda p: f"{p.age}", ignore=True),
    "age_es": S(None, lambda p: f"{p.age} años", ignore=True),
    "country": S(None, lambda p: p.rng.choice(["Mexico", "Guatemala", "El Salvador", "Vietnam"]), ignore=True),
}

_ACCENT_RULES = [("Th", "T"), ("th", "t"), ("z", "s"), ("v", "b"), ("ll", "y"), ("son", "sson"),
                 ("W", "Gu"), ("ck", "k")]


def _accent(s: str) -> str:
    """Deterministic 'Spanish-accent' ASR-style spelling variation (e.g. Thompson -> Tompsson)."""
    for a, b in _ACCENT_RULES:
        s = s.replace(a, b)
    return s
