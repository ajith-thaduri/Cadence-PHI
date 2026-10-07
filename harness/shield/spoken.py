"""Spoken-digit normalisation (English + Spanish ASR output).

Finds runs of digit words / digit strings such as "five five five, four one zero" or
"cinco cinco cinco" and returns (start, end, digits) with exact character offsets.
"""
from __future__ import annotations

import re

DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "cero": "0", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
    "seis": "6", "siete": "7", "ocho": "8", "nueve": "9",
}
MULT = {"double": 2, "triple": 3, "doble": 2, "triple_es": 3}
_TOKEN = re.compile(r"[A-Za-zÀ-ÿ]+|\d+")
_SEP = re.compile(r"^[\s,.\-]*(?:dash\s*|guión\s*)?[\s,.\-]*$")


def digit_runs(text: str, min_digits: int = 4) -> list[tuple[int, int, str]]:
    toks = [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(toks):
        run_s, run_e, digits, j, pending_mult = None, None, "", i, 1
        while j < len(toks):
            s, e, w = toks[j]
            lw = w.lower()
            if run_e is not None and not _SEP.match(text[run_e:s]):
                break
            if lw in MULT and j + 1 < len(toks) and toks[j + 1][2].lower() in DIGIT_WORDS:
                pending_mult = MULT[lw]
                run_s = s if run_s is None else run_s
                run_e = e
                j += 1
                continue
            if lw in DIGIT_WORDS and not (lw == "oh" and not digits):
                digits += DIGIT_WORDS[lw] * pending_mult
            elif w.isdigit() and len(w) <= 4 and digits:
                digits += w
            else:
                break
            pending_mult = 1
            run_s = s if run_s is None else run_s
            run_e = e
            j += 1
        if digits and len(digits) >= min_digits:
            out.append((run_s, run_e, digits))
            i = j
        else:
            i += 1
    return out
