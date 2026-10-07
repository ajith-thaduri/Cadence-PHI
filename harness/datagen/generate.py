"""Generate the Synthetic Cadence Benchmark v2 (dev + test splits).

Deterministic: same seeds -> byte-identical files. Models are never trained on any split
(no train split is produced: nothing is trained). Dev and test use DISJOINT template
families; dev is used only to choose thresholds and to develop deterministic rules.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

from harness.datagen.builder import make_sample
from harness.datagen.noise import Noise
from harness.datagen.templates import (
    LONG_FILLER, LONG_PHI_DEV, LONG_PHI_TEST, LONG_SIZES_TOKENS, SUBSET_LANGUAGE, SUBSET_NOISE, TEMPLATES,
)
from harness.datagen.values import FACILITIES, Persona

GENERATOR_VERSION = "2.0.0"
DATASET_NAME = "Synthetic Cadence Benchmark v2"
SPLITS = {
    # split: (seed, docs per regular subset, long sizes, long docs per size)
    "dev": (7_770_002, 6, [500, 2000, 8000], 1),
    "test": (20_261_002, 12, LONG_SIZES_TOKENS, 2),
}
SHORT = {"CLEAN_CLINICAL": "clin", "NOISY_ASR": "asr", "TELEPHONE_CONVERSATION": "tel",
         "FACE_TO_FACE_DICTATION": "f2f", "CARE_PLAN": "plan", "MEMBER_SUMMARY": "summ",
         "TIMELINE": "time", "CARE_ASSISTANT": "assist", "CONTEXTUAL_PHI": "ctx", "SPANISH": "es",
         "MIXED_ENGLISH_SPANISH": "mix", "HARD_IDENTIFIERS": "hard", "DATE_FORMS": "date",
         "NEGATIVE_CONTROL": "neg", "LONG_DOCUMENTS": "long"}
WORDPIECES_PER_WORD = 1.35   # rough estimate used only to size long documents


def _pick(rng: random.Random, dist: list[tuple[str, float]]) -> str:
    x, acc = rng.random(), 0.0
    for v, w in dist:
        acc += w
        if x <= acc:
            return v
    return dist[-1][0]


def registry_context(p: Persona, rng: random.Random) -> dict[str, Any]:
    """What Cadence's member / provider / facility registries would know for this document.

    Deliberately incomplete, as in production: relatives appear only sometimes (as an
    emergency contact), employer only sometimes, the second provider (provider2) and the
    second facility (facility2) are never registered (out-of-network references).
    """
    others = [f for f, _ in FACILITIES if f not in (p.facility, p.facility2)]
    reg: dict[str, Any] = {
        "member": {"name": p.patient, "dob": p.dob.isoformat(), "member_ids": [p.member_id, p.member_alnum],
                   "mrn": p.mrn, "phone": p.phone_digits, "email": p.email,
                   "address": f"{p.street_no} {p.street[0]} {p.street[1]}", "city": p.city[0], "zip": p.zip},
        "providers": [{"name": f"{p.provider_first} {p.provider_last}", "npi": p.npi}],
        "staff": [p.care_team],
        "facilities": [{"name": p.facility, "aliases": [p.facility_short]}] +
                      [{"name": f, "aliases": []} for f in rng.sample(others, 2)],
        "employers": [p.employer] if rng.random() < 0.5 else [],
        "contacts": [f"{p.relative_first} {p.relative_last.split()[0]}"] if rng.random() < 0.4 else [],
    }
    return reg


def _meta(subset: str, split: str, noise: str, template_id: str, tags: list[str], extra=None) -> dict[str, Any]:
    md = {"subset": subset, "language": SUBSET_LANGUAGE.get(subset, "en"), "noise_level": noise,
          "source": "synthetic", "dataset": DATASET_NAME, "split": split, "template_family": split,
          "template_id": template_id, "phenomena": sorted(set(tags)),
          "generator_version": GENERATOR_VERSION}
    md.update(extra or {})
    return md


def _long_template(rng: random.Random, target_tokens: int, phi_sentences: list[str]) -> str:
    target_words = int(target_tokens / WORDPIECES_PER_WORD)
    n_phi = min(20, 4 + target_tokens // 600)
    gap = max(20, target_words // n_phi)
    parts, words, since_phi, phi_i = [], 0, gap // 2, 0
    while words < target_words:
        if since_phi >= gap:
            s = phi_sentences[phi_i % len(phi_sentences)]
            phi_i += 1
            since_phi = 0
        else:
            s = rng.choice(LONG_FILLER)
            since_phi += len(s.split())
        parts.append(s)
        words += len(s.split())
    parts.append(phi_sentences[phi_i % len(phi_sentences)])
    return " ".join(parts)


def generate_split(split: str):
    seed, per_subset, long_sizes, per_size = SPLITS[split]
    rng = random.Random(seed)
    samples = []
    for subset, fams in TEMPLATES.items():
        templates = fams[split]
        order = list(range(len(templates)))
        rng.shuffle(order)
        for i in range(per_subset):
            tid, tpl, tags = templates[order[i % len(order)]]
            noise_level = _pick(rng, SUBSET_NOISE[subset])
            lang = SUBSET_LANGUAGE.get(subset, "en")
            spanish = lang == "es" or (lang == "mixed" and rng.random() < 0.5)
            persona = Persona.sample(random.Random(rng.random()), spanish=spanish)
            noise = Noise(noise_level, random.Random(rng.random()), "es" if lang == "es" else "en")
            sid = f"{SHORT[subset]}_{split}_{i + 1:03d}"
            reg = registry_context(persona, random.Random(rng.random()))
            samples.append(make_sample(sid, tpl, persona, noise,
                                       _meta(subset, split, noise_level, tid, tags, {"registry": reg})))
    phi = LONG_PHI_DEV if split == "dev" else LONG_PHI_TEST
    for size in long_sizes:
        for j in range(per_size if size <= 8000 else 1):
            noise_level = _pick(rng, SUBSET_NOISE["LONG_DOCUMENTS"])
            persona = Persona.sample(random.Random(rng.random()))
            noise = Noise(noise_level, random.Random(rng.random()))
            tpl = _long_template(random.Random(rng.random()), size, phi)
            sid = f"long{size}_{split}_{j + 1:03d}"
            reg = registry_context(persona, random.Random(rng.random()))
            samples.append(make_sample(sid, tpl, persona, noise, _meta(
                "LONG_DOCUMENTS", split, noise_level, f"long_{size}_{split}", ["long_document"],
                {"target_tokens": size, "words": len(tpl.split()), "registry": reg})))
    return samples


def write_jsonl(samples, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s.model_dump(), ensure_ascii=False) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_all(out_dir: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {"dataset": DATASET_NAME, "generator_version": GENERATOR_VERSION,
                                "source": "synthetic (fully fabricated; no real PHI)",
                                "evidence_label": "SYNTHETIC DATA / OUR INTERNAL TEST",
                                "template_families": "dev and test use disjoint template families",
                                "splits": {}}
    for split in SPLITS:
        samples = generate_split(split)
        path = out_dir / f"{split}.jsonl"
        digest = write_jsonl(samples, path)
        by_subset: dict[str, int] = {}
        by_label: dict[str, int] = {}
        for s in samples:
            by_subset[s.subset] = by_subset.get(s.subset, 0) + 1
            for e in s.entities:
                by_label[e.label] = by_label.get(e.label, 0) + 1
        manifest["splits"][split] = {
            "path": f"data/synthetic/{split}.jsonl", "sha256": digest, "seed": SPLITS[split][0],
            "documents": len(samples), "entities": sum(len(s.entities) for s in samples),
            "template_ids": sorted({s.metadata["template_id"] for s in samples}),
            "by_subset": dict(sorted(by_subset.items())), "by_label": dict(sorted(by_label.items())),
        }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
