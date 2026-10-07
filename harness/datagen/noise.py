"""Length-changing noise transforms applied per segment BEFORE offsets are computed.

Because the builder concatenates segments after transforming them, gold offsets stay
exact by construction (validated again in Sample.validate_offsets).
"""
from __future__ import annotations

import random
import re

FILLERS_EN = ["uh", "um", "you know", "like", "I mean", "so"]
FILLERS_ES = ["eh", "este", "o sea", "pues", "bueno"]
HOMOPHONES = {"their": "there", "to": "too", "hear": "here", "for": "four", "right": "write",
              "know": "no", "week": "weak", "see": "sea", "would": "wood"}
_PUNCT = re.compile(r"[.,;:!?()\"]")


class Noise:
    def __init__(self, level: str, rng: random.Random, lang: str = "en") -> None:
        self.level = level
        self.rng = rng
        self.fillers = FILLERS_ES if lang == "es" else FILLERS_EN

    @property
    def asr(self) -> bool:
        return self.level in ("medium", "high")

    def literal(self, s: str) -> str:
        if self.level == "clean":
            return s
        r = self.rng
        if self.level == "low":
            s = s.replace(", ", " ", 1) if r.random() < 0.5 else s
            return s
        # medium / high: lowercase, drop punctuation, fillers, repeats, homophones
        s = _PUNCT.sub("", s.lower())
        p_fill = 0.08 if self.level == "medium" else 0.16
        p_rep = 0.03 if self.level == "medium" else 0.07
        out = []
        for idx, w in enumerate(s.split(" ")):
            if w and r.random() < p_fill:
                # a filler at the very start of a segment must not glue onto the preceding entity
                out.append(("" if idx else " ") + r.choice(self.fillers))
            if w and self.level == "high" and w in HOMOPHONES and r.random() < 0.5:
                w = HOMOPHONES[w]
            out.append(w)
            if w and r.random() < p_rep:
                out.append(w)
        s = " ".join(out)
        if self.level == "high" and r.random() < 0.3 and len(s.split()) > 4:
            words = s.split(" ")
            k = r.randrange(1, len(words) - 1)
            if words[k]:
                words[k] = words[k][: max(1, len(words[k]) // 2)] + "- " + words[k]
            s = " ".join(words)
        return re.sub(r" {2,}", " ", s)

    def entity(self, s: str, label: str) -> str:
        if self.level in ("clean", "low"):
            return s
        s = s.lower()
        if label != "EMAIL" and label != "URL":
            s = _PUNCT.sub("", s).replace("  ", " ")
            if label in ("PHONE",):
                s = s.replace("-", " ")
        return s.strip()
