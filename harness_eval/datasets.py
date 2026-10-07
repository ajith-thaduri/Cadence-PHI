"""Benchmark datasets -> (DetectionRequest, GoldDocument) pairs + a scoped RegistryStore.

Gold never enters a DetectionRequest. Each document's registry (authored, NOT gold-derived)
becomes a subject record in an InMemoryRegistryStore, and the request refers to it only by an
opaque subject_context_id. Gold-derived registries (simulated_from_gold) are dropped.

Request settings chosen here are DATA decisions, made explicitly per document:
  modality              asr (ASR datasets) | telephone (spoken transcript) | dictation | note | typed
  normalization_profile asr / spanish_asr for spoken documents, default for written ones
Scoring refinement D3 (edge function words; docs/rule_changelog.md) is applied to gold here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from harness.datasets import load_dataset
from harness.paths import ROOT
from harness.phi.contracts import DetectionOptions, DetectionRequest, GoldDocument, GoldEntity, Mode, stable_id
from harness.phi.registry_store import InMemoryRegistryStore

GROUPS = {"cadence_dev": ["cadence_dev_text", "cadence_dev_asr"], "cadence_test": ["cadence_text", "cadence_asr"]}
DEV_DATASETS = {"cadence_dev_text", "cadence_dev_asr", "synthetic_dev", "meddocan_dev"}
TEST_DATASETS = {"cadence_text", "cadence_asr", "cadence_asr_uncorrected", "meddocan_test", "synthetic_test"}
EDGE_WORDS = {"the", "a", "an", "on", "in", "at", "around", "about", "by", "of", "from", "since", "until",
              "el", "la", "los", "las", "lo", "del", "de", "en", "un", "una", "desde", "hasta", "para", "y", "and"}
_EDGE_TOK = re.compile(r"[A-Za-zÀ-ÿ]+")
ASR_LOSS_FILES = {"cadence_asr": ("asr_dropped_entities.json", "asr_garbled_entities.json"),
                  "cadence_dev_asr": ("cadence_dev_asr_dropped_entities.json", "cadence_dev_asr_garbled_entities.json")}


def expand(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        out += GROUPS.get(n, [n])
    return list(dict.fromkeys(out))


def trim_edges(text: str, s0: int, e0: int) -> tuple[int, int]:
    """D3: drop lowercase articles/prepositions at the edges of a gold span."""
    while True:
        m = _EDGE_TOK.match(text, s0)
        if m and m.end() < e0 and m.group() in EDGE_WORDS and not text[m.end()].isalnum():
            s0 = m.end()
            while s0 < e0 and not text[s0].isalnum():
                s0 += 1
            continue
        break
    while True:
        m = re.search(r"([A-Za-zÀ-ÿ]+)[^A-Za-zÀ-ÿ0-9]*$", text[s0:e0])
        if m and m.start(1) > 0 and m.group(1) in EDGE_WORDS:
            e0 = s0 + m.start(1)
            while e0 > s0 and not text[e0 - 1].isalnum():
                e0 -= 1
            continue
        break
    return s0, e0


def _modality(ds_name: str, md: dict[str, Any]) -> str:
    if "asr" in ds_name:
        return "asr"
    dt = md.get("doc_type", "")
    if dt == "dictation":
        return "dictation"
    if md.get("spoken"):
        return "telephone"
    return "note" if dt in ("care_note", "timeline", "member_summary") else "typed"


def _profile(md: dict[str, Any], lang: str) -> str:
    if md.get("spoken"):
        return "asr"
    return "default"


def load(ds_name: str, mode: Mode = Mode.SCORE, edge_policy: str = "trim_edge_function_words",
         languages: Optional[list[str]] = None
         ) -> tuple[list[DetectionRequest], list[GoldDocument], InMemoryRegistryStore, Any]:
    """languages: keep only documents in these languages (this phase: English only). Documents
    outside scope are excluded explicitly and reported by the caller, never silently scored."""
    ds = load_dataset(ds_name)
    if languages is not None:
        ds.samples = [s for s in ds.samples if (s.language or s.metadata.get("language") or "en") in set(languages)]
    store = InMemoryRegistryStore()
    lost: dict[str, list[dict]] = {}
    if ds_name in ASR_LOSS_FILES:
        for fname, kind in zip(ASR_LOSS_FILES[ds_name], ("deleted", "garbled")):
            p = ROOT / "data" / "cadence_realistic" / fname
            for r in json.loads(p.read_text()) if p.exists() else []:
                lost.setdefault(r["doc"], []).append({"entity_type": r["label"], "kind": kind})
    reqs, golds = [], []
    for s in ds.samples:
        md = s.metadata
        lang = s.language or md.get("language") or "en"
        rid = stable_id("req", ds_name, s.id)
        reg = md.get("registry")
        ctx = None
        if reg and not reg.get("simulated_from_gold"):
            sid = stable_id("ctx", ds_name, s.id)
            store.add_subject(sid, reg)
            ctx = {"subject_context_id": sid}
        reqs.append(DetectionRequest(request_id=rid, text=s.text, language=lang,
                                     modality=_modality(ds_name, md), source="evaluation_dataset",
                                     normalization_profile=_profile(md, lang), registry_context=ctx,
                                     policy_profile="evaluation", options=DetectionOptions(mode=mode)))
        ents = []
        for i, g in enumerate(s.entities):
            a, b = (trim_edges(s.text, g.start, g.end) if edge_policy == "trim_edge_function_words" else (g.start, g.end))
            if b <= a:
                a, b = g.start, g.end
            ents.append(GoldEntity(annotation_id=f"{s.id}#{i}", entity_type=g.label, start=a, end=b, source=ds_name,
                                   edge_policy=edge_policy, tags=list(g.tags or [])))
        for j, ig in enumerate(s.ignore_spans):
            ents.append(GoldEntity(annotation_id=f"{s.id}#ign{j}", entity_type="IGNORE", start=ig.start, end=ig.end,
                                   source=ds_name, ignore_region=True, tags=[ig.reason[:40]]))
        src_id = md.get("source_id") or s.id
        golds.append(GoldDocument(request_id=rid, dataset=ds_name, entities=ents, asr_lost=lost.get(src_id, []),
                                  metadata={"doc_id": s.id, "language": lang, "doc_type": md.get("doc_type"),
                                            "spoken": md.get("spoken"), "source_id": src_id, "data_source": ds.source}))
    return reqs, golds, store, ds


def write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write((r.model_dump_json() if hasattr(r, "model_dump_json") else json.dumps(r, ensure_ascii=False)) + "\n")


def export(ds_name: str, out: Path, languages: Optional[list[str]] = None) -> dict[str, Path]:
    """Canonical interchange files: <ds>.requests.jsonl, <ds>.gold.jsonl, registry/subjects/*.json (local only)."""
    reqs, golds, store, _ = load(ds_name, Mode.DETECT, languages=languages)
    paths = {"requests": out / f"{ds_name}.requests.jsonl", "gold": out / f"{ds_name}.gold.jsonl",
             "registry": out / "registry"}
    write_jsonl(paths["requests"], reqs)
    write_jsonl(paths["gold"], golds)
    (paths["registry"] / "subjects").mkdir(parents=True, exist_ok=True)
    for sid, rec in store._subjects.items():
        (paths["registry"] / "subjects" / f"{sid}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return paths


def read_requests(path: Path) -> list[DetectionRequest]:
    return [DetectionRequest.model_validate_json(l) for l in Path(path).read_text().splitlines() if l.strip()]


def read_gold(path: Path) -> list[GoldDocument]:
    return [GoldDocument.model_validate_json(l) for l in Path(path).read_text().splitlines() if l.strip()]


def is_dev(name: str) -> bool:
    return name in DEV_DATASETS or name.startswith("cadence_dev")


def dataset_of(path_or_name: str) -> Optional[str]:
    n = Path(path_or_name).name.split(".")[0]
    return n if n in DEV_DATASETS | TEST_DATASETS else None
