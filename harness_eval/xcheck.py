"""Independent cross-check of the leak metric (plan G-25): a deliberately separate, minimal
implementation of token-level PHI recall on the redacted output. It shares no code with
harness_eval.scoring except the input objects; the two must agree (tests/test_eval_core.py).
presidio-research's TokenEvaluator can be plugged in here when installed; it is not a dependency."""
from __future__ import annotations

import re

WORD = re.compile(r"[A-Za-z0-9À-ɏ]+")


def token_recall(text: str, gold_spans: list[tuple[int, int]], hidden: set[int]) -> tuple[int, int]:
    """(protected PHI tokens, total PHI tokens): a PHI token is protected only if every one of its
    characters is hidden in the output."""
    total = protected = 0
    for m in WORD.finditer(text):
        if any(s <= m.start() < e or s < m.end() <= e for s, e in gold_spans):
            total += 1
            protected += all(i in hidden for i in range(m.start(), m.end()))
    return protected, total
