"""Fabricated value pools for the Synthetic Cadence Benchmark (v2).

Every person/identifier value is FABRICATED. v2 uses NATURAL formats (the v1 benchmark
over-used stylised TEST-MBR-/MRN- prefixes). Safeguards that keep values fake:
  - US phones use the 555-0100..0199 fictional range (e.g. (312) 555-0147) or area code 555
  - e-mail and URLs use reserved example.com / example.org / example.net domains
  - 10-digit NPIs deliberately FAIL the NPI Luhn check (cannot be a real NPI)
  - Spanish DNI-like numbers use a deliberately WRONG check letter
  - Spanish phones use the unassigned 555 prefix
Real organisation names (e.g. Walmart) and common street/city names appear only as
generic context, never tied to a real person.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

FIRST_F = ["Maria", "Jessica", "Linda", "Rosa", "Ana", "Patricia", "Keisha", "Mei", "Fatima",
           "Gloria", "Denise", "Carmen", "Yolanda", "Priya", "Elena", "Teresa", "Brenda", "Lucia",
           "Sarah", "Dorothy", "Tamika", "Nguyet", "Shirley", "Ruth"]
FIRST_M = ["Daniel", "Michael", "James", "Luis", "Robert", "Tomas", "Andre", "Wei", "Omar",
           "Victor", "Harold", "Jorge", "Marcus", "Raj", "Eduardo", "Samuel", "Curtis", "Diego",
           "Carlos", "William", "Earl", "Darnell", "Hung", "Walter"]
LAST = ["Thompson", "Rodriguez", "Carter", "Nguyen", "Okafor", "Delgado", "Whitfield", "Ramirez",
        "Kowalski", "Haddad", "Bennett", "Castillo", "Fairbanks", "Morales", "Pemberton", "Ibarra",
        "Lindqvist", "Ashworth", "Villanueva", "Washington", "Jefferson", "Patel", "Kim", "Brooks",
        "Coleman", "Sandoval", "Mendoza", "Gutierrez", "Hughes", "Price"]
NICKNAMES = {"Robert": "Bob", "William": "Bill", "Patricia": "Pat", "Michael": "Mike",
             "James": "Jim", "Daniel": "Dan", "Samuel": "Sam", "Dorothy": "Dot", "Walter": "Walt",
             "Jessica": "Jess", "Elena": "Lena", "Teresa": "Tere"}
# Spanish-speaking members (compound given names + two surnames)
FIRST_ES_F = ["María José", "Guadalupe", "Ana Lucía", "Rosario", "Carmen", "Josefina", "Leticia", "Marisol"]
FIRST_ES_M = ["Juan Carlos", "José Luis", "Francisco", "Miguel Ángel", "Rafael", "Héctor", "Ramón", "Jesús"]
LAST_ES = ["Hernández", "García", "Martínez", "López", "González", "Pérez", "Sánchez", "Ramírez",
           "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Cruz", "Morales", "Reyes"]

RELATIONS_EN = ["daughter", "son", "husband", "wife", "sister", "brother", "niece", "grandson",
                "granddaughter", "neighbor"]
RELATIONS_ES = ["hija", "hijo", "esposo", "esposa", "hermana", "hermano", "sobrina", "nieto",
                "nieta", "vecina"]

FACILITIES = [("Sunrise Valley Medical Center", "Sunrise Valley"),
              ("Valley Memorial Hospital", "Valley Memorial"),
              ("St. Joseph's Regional Medical Center", "St. Joseph's"),
              ("Mercy Family Clinic", "Mercy"),
              ("Riverside Community Health Center", "Riverside"),
              ("Lakeshore General Hospital", "Lakeshore General"),
              ("Northgate Dialysis Center", "Northgate"),
              ("Cedar Grove Rehabilitation Center", "Cedar Grove")]
FACILITIES_ES = [("Hospital Universitario La Paz", "La Paz"), ("Hospital General de Villalba", "Villalba"),
                 ("Centro de Salud Las Águilas", "Las Águilas"), ("Clínica San Rafael", "San Rafael")]
STREETS = [("Oak", "Street", "St"), ("Maple", "Avenue", "Ave"), ("Main", "Street", "St"),
           ("Jefferson", "Boulevard", "Blvd"), ("Cedar", "Lane", "Ln"), ("Lincoln", "Avenue", "Ave"),
           ("Hillcrest", "Drive", "Dr"), ("Washington", "Road", "Rd"), ("Pine Ridge", "Court", "Ct"),
           ("Elm", "Way", "Way"), ("Sunset", "Boulevard", "Blvd"), ("Park", "Place", "Pl")]
STREETS_ES = ["Calle Mayor", "Avenida de la Constitución", "Calle del Olivo", "Paseo de la Estación",
              "Calle San Juan", "Avenida Andalucía", "Plaza de España"]
CITIES = [("Springfield", "Illinois", "IL"), ("Riverside", "California", "CA"), ("Fairview", "Texas", "TX"),
          ("Franklin", "Tennessee", "TN"), ("Greenville", "South Carolina", "SC"), ("Madison", "Wisconsin", "WI"),
          ("Clinton", "Iowa", "IA"), ("Georgetown", "Kentucky", "KY"), ("Salem", "Oregon", "OR"),
          ("Albuquerque", "New Mexico", "NM"), ("Phoenix", "Arizona", "AZ"), ("Chicago", "Illinois", "IL")]
CITIES_ES = [("Madrid", "28013"), ("Sevilla", "41004"), ("Valencia", "46001"), ("Zaragoza", "50001"),
             ("Málaga", "29005"), ("Toledo", "45001")]
AREA_CODES = ["312", "773", "415", "213", "602", "505", "214", "704", "617", "206"]
LANDMARKS = ["the Walgreens on Fifth", "Lincoln Park", "the old Kmart plaza", "the county fairgrounds",
             "Riverside Mall", "the Greyhound station"]
EMPLOYERS = ["Walmart", "Home Depot", "Amazon", "Lincoln Elementary School", "Kroger", "Tyson Foods",
             "the county bus depot", "Target"]
ORGS = ["Blue Horizon Health Plan", "Bright Path Home Health", "Meals on Wheels", "CVS Pharmacy",
        "the Area Agency on Aging", "Molina Healthcare"]
LANGUAGES_EN = ["Spanish", "Vietnamese", "Tagalog", "Portuguese", "Haitian Creole", "Mandarin", "Somali"]
LANGUAGES_ES = ["español", "inglés", "portugués", "vietnamita"]
CARE_TEAM = ["Priya Natarajan", "Gloria Whitfield", "Samuel Okafor", "Lucia Ibarra", "Marcus Bennett",
             "Sarah Coleman", "Tamika Brooks"]
DOMAINS = ["example.com", "example.org", "example.net"]

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

DIGITS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
DIGITS_ES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
_ONES = DIGITS_EN + ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                     "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth"}
_ES_DAYS = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
            "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho",
            "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro",
            "veinticinco", "veintiséis", "veintisiete", "veintiocho"]


def num_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("" if o == 0 else " " + _ONES[o])
    h, r = divmod(n, 100)
    return _ONES[h] + " hundred" + ("" if r == 0 else " " + num_words(r))


def ordinal_words(n: int) -> str:
    w = num_words(n).split(" ")
    last = w[-1]
    if last in _ORD:
        w[-1] = _ORD[last]
    elif last.endswith("y"):
        w[-1] = last[:-1] + "ieth"
    else:
        w[-1] = last + "th"
    return " ".join(w)


def year_words(y: int) -> str:
    hi, lo = divmod(y, 100)
    if y >= 2000 and lo < 10:
        return "two thousand" + ("" if lo == 0 else " " + num_words(lo))
    return num_words(hi) + " " + ("hundred" if lo == 0 else ("oh " + num_words(lo) if lo < 10 else num_words(lo)))


def spoken_digits(s: str, lang: str = "en", group_sep: str = " ") -> str:
    table = DIGITS_ES if lang == "es" else DIGITS_EN
    groups = []
    for grp in s.replace("(", " ").replace(")", " ").replace("-", " ").replace(".", " ").split():
        groups.append(" ".join(table[int(c)] for c in grp if c.isdigit()))
    return group_sep.join(g for g in groups if g)


def ordinal_suffix(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def es_day(n: int) -> str:
    return _ES_DAYS[n] if 0 < n < len(_ES_DAYS) else str(n)


def _luhn_ok_npi(d10: str) -> bool:
    digits = [int(c) for c in "80840" + d10]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total % 10 == 0


def fake_npi(rng: random.Random) -> str:
    while True:
        d = rng.choice("12") + "".join(str(rng.randint(0, 9)) for _ in range(9))
        if not _luhn_ok_npi(d):
            return d


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def fake_dni(rng: random.Random) -> str:
    n = rng.randint(10_000_000, 99_999_999)
    wrong = [c for c in _DNI_LETTERS if c != _DNI_LETTERS[n % 23]]
    return f"{n}{rng.choice(wrong)}"


@dataclass
class Persona:
    """All fabricated identifiers for one synthetic document (consistent within a doc)."""
    rng: random.Random
    spanish: bool = False
    patient_first: str = ""
    patient_last: str = ""
    patient_gender: str = "f"
    relative_first: str = ""
    relative_last: str = ""
    relation: str = ""
    relation_es: str = ""
    provider_first: str = ""
    provider_last: str = ""
    provider_female: bool = False
    provider2_first: str = ""
    provider2_last: str = ""
    facility: str = ""
    facility_short: str = ""
    facility2: str = ""
    care_team: str = ""
    member_id: str = ""
    member_alnum: str = ""
    mrn: str = ""
    npi: str = ""
    account: str = ""
    claim_id: str = ""
    dni: str = ""
    area: str = ""
    line: str = ""
    phone_es: str = ""
    street_no: int = 0
    street: tuple = ("", "", "")
    street2: tuple = ("", "", "")
    street_es: str = ""
    unit: str = ""
    city: tuple = ("", "", "")
    city_es: tuple = ("", "")
    zip: str = ""
    landmark: str = ""
    employer: str = ""
    org: str = ""
    language_en: str = ""
    language_es: str = ""
    email_user: str = ""
    domain: str = ""
    dob: date = date(1970, 1, 1)
    visit: date = date(2026, 1, 1)
    age: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def sample(cls, rng: random.Random, spanish: bool = False) -> "Persona":
        p = cls(rng=rng, spanish=spanish)
        p.patient_gender = rng.choice("fm")
        if spanish:
            p.patient_first = rng.choice(FIRST_ES_F if p.patient_gender == "f" else FIRST_ES_M)
            p.patient_last = " ".join(rng.sample(LAST_ES, 2))
            p.relative_first = rng.choice(FIRST_ES_F + FIRST_ES_M).split()[0]
            p.relative_last = p.patient_last.split()[0]
            fac = rng.choice(FACILITIES_ES)
        else:
            p.patient_first = rng.choice(FIRST_F if p.patient_gender == "f" else FIRST_M)
            p.patient_last = rng.choice(LAST)
            p.relative_first = rng.choice([n for n in FIRST_F + FIRST_M if n != p.patient_first])
            p.relative_last = p.patient_last
            fac = rng.choice(FACILITIES)
        i = rng.randrange(len(RELATIONS_EN))
        p.relation, p.relation_es = RELATIONS_EN[i], RELATIONS_ES[i]
        p.provider_female = rng.random() < 0.5
        p.provider_first = rng.choice(FIRST_F if p.provider_female else FIRST_M)
        p.provider_last = rng.choice([n for n in LAST if n not in p.patient_last])
        p.provider2_first = rng.choice(FIRST_F + FIRST_M)
        p.provider2_last = rng.choice([n for n in LAST if n not in (p.patient_last, p.provider_last)])
        p.facility, p.facility_short = fac
        p.facility2 = rng.choice([f for f, _ in FACILITIES if f != p.facility])
        p.care_team = rng.choice(CARE_TEAM)
        p.member_id = str(rng.randint(10_000_000, 999_999_999))
        p.member_alnum = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3)) + str(rng.randint(100_000_000, 999_999_999))
        p.mrn = str(rng.randint(100_000, 99_999_999))
        p.npi = fake_npi(rng)
        p.account = str(rng.randint(10_000_000, 99_999_999))
        p.claim_id = f"{rng.randint(2025, 2026)}{rng.randint(10_000_000, 99_999_999)}"
        p.dni = fake_dni(rng)
        p.area = rng.choice(AREA_CODES)
        p.line = f"01{rng.randint(0, 99):02d}"
        p.phone_es = f"555 {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)}"
        p.street_no = rng.choice([rng.randint(100, 9999), rng.randint(10, 99)])
        p.street = rng.choice(STREETS)
        p.street2 = rng.choice([s for s in STREETS if s != p.street])
        p.street_es = rng.choice(STREETS_ES)
        p.unit = rng.choice(["Apt 3B", "Unit 12", "#204", "Apt. 7", ""])
        p.city = rng.choice(CITIES)
        p.city_es = rng.choice(CITIES_ES)
        p.zip = f"{rng.randint(10000, 99950)}"
        p.landmark = rng.choice(LANDMARKS)
        p.employer = rng.choice(EMPLOYERS)
        p.org = rng.choice(ORGS)
        p.language_en = rng.choice(LANGUAGES_EN)
        p.language_es = rng.choice(LANGUAGES_ES)
        fl = (p.patient_first.split()[0] + "." + p.patient_last.split()[0]).lower()
        fl = fl.translate(str.maketrans("áéíóúñü", "aeiounu"))
        p.email_user = rng.choice([fl, fl.replace(".", "") + str(rng.randint(10, 99)), fl[0] + fl.split(".")[1]])
        p.domain = rng.choice(DOMAINS)
        p.dob = date(rng.randint(1935, 1995), rng.randint(1, 12), rng.randint(1, 28))
        p.visit = date(2026, 1, 1) + timedelta(days=rng.randint(0, 360))
        p.age = 2026 - p.dob.year
        return p

    # ---- derived strings ---------------------------------------------------------------
    @property
    def patient(self) -> str:
        return f"{self.patient_first} {self.patient_last}"

    @property
    def phone_digits(self) -> str:
        return f"{self.area}555{self.line}"

    @property
    def email(self) -> str:
        return f"{self.email_user}@{self.domain}"

    @property
    def title_es(self) -> str:
        return "Dra." if self.provider_female else "Dr."

    @property
    def nickname(self) -> str:
        first = self.patient_first.split()[0]
        return NICKNAMES.get(first, first)
