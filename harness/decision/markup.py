"""Inline PHI markup used by the Tier-3 Cadence-realistic set.

Authored text marks every PHI span as  [[LABEL:surface text]]  e.g.
    "CM: Hi, is this [[NAME:Rosa]]? ... call me back at [[PHONE:seven seven three five five five zero one four two]]"
`parse()` strips the markup and returns clean text + exact gold character offsets.

Validate a file:  python -m harness.decision.markup data/cadence_realistic/authored/<file>.jsonl
"""
from __future__ import annotations

import json
import re
import sys

from harness.taxonomy import CANONICAL_LABELS

MARK = re.compile(r"\[\[([A-Z_]+):([^\[\]]+?)\]\]")


def parse(marked: str) -> tuple[str, list[dict]]:
    out, ents, pos, last = [], [], 0, 0
    for m in MARK.finditer(marked):
        lit = marked[last:m.start()]
        out.append(lit)
        pos += len(lit)
        label, surface = m.group(1), m.group(2)
        if label not in CANONICAL_LABELS:
            raise ValueError(f"unknown label {label!r} in {m.group(0)!r}")
        out.append(surface)
        ents.append({"start": pos, "end": pos + len(surface), "text": surface, "label": label})
        pos += len(surface)
        last = m.end()
    out.append(marked[last:])
    text = "".join(out)
    if "[[" in text or "]]" in text:
        raise ValueError("malformed markup (unbalanced [[ ]])")
    return text, ents


REQUIRED = {"id", "doc_type", "language", "text", "registry"}


def validate_file(path: str) -> None:
    n = ents = 0
    labels: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            rec = json.loads(line)
            missing = REQUIRED - set(rec)
            if missing:
                raise ValueError(f"line {i}: missing fields {missing}")
            text, es = parse(rec["text"])
            for e in es:
                assert text[e["start"]:e["end"]] == e["text"]
                labels[e["label"]] = labels.get(e["label"], 0) + 1
            n += 1
            ents += len(es)
    print(f"OK {path}: {n} documents, {ents} PHI spans, labels={dict(sorted(labels.items()))}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        validate_file(p)
