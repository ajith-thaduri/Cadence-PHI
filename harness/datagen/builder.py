"""Template renderer producing text + exact gold spans."""
from __future__ import annotations

import re
from typing import Any

from harness.datagen.noise import Noise
from harness.datagen.slots import SLOTS
from harness.datagen.values import Persona
from harness.schema import GoldEntity, IgnoreSpan, Sample

_SLOT = re.compile(r"\{([a-z0-9_]+)\}")


def render(template: str, persona: Persona, noise: Noise) -> tuple[str, list[GoldEntity], list[IgnoreSpan]]:
    out: list[str] = []
    pos = 0
    ents: list[GoldEntity] = []
    ign: list[IgnoreSpan] = []
    last = 0
    for m in _SLOT.finditer(template):
        lit = noise.literal(template[last:m.start()])
        out.append(lit)
        pos += len(lit)
        name = m.group(1)
        if name not in SLOTS:
            raise KeyError(f"unknown slot {{{name}}} in template: {template[:60]!r}")
        slot = SLOTS[name]
        val = slot.render(persona)
        if slot.label:
            val = noise.entity(val, slot.label)
        out.append(val)
        if slot.ignore:
            ign.append(IgnoreSpan(start=pos, end=pos + len(val), reason=f"no canonical label ({name})"))
        elif slot.label:
            tags = list(slot.tags) + (["asr_noise"] if noise.asr else [])
            ents.append(GoldEntity(start=pos, end=pos + len(val), text=val, label=slot.label,
                                   difficulty=slot.difficulty, tags=sorted(set(tags))))
        pos += len(val)
        last = m.end()
    tail = noise.literal(template[last:])
    out.append(tail)
    return "".join(out), ents, ign


def make_sample(sid: str, template: str, persona: Persona, noise: Noise, metadata: dict[str, Any]) -> Sample:
    text, ents, ign = render(template, persona, noise)
    # collapse accidental double spaces at segment joins without moving entity offsets:
    # re-render is not needed because noise.literal already normalises inside segments.
    diffs = [e.difficulty for e in ents]
    order = {"easy": 0, "medium": 1, "hard": 2}
    md = dict(metadata)
    md.setdefault("difficulty", max(diffs, key=lambda d: order[d]) if diffs else "easy")
    s = Sample(id=sid, text=text, entities=ents, ignore_spans=ign, metadata=md)
    s.validate_offsets()
    return s
