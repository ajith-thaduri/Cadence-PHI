"""MEDDOCAN (IberLEF 2019) loader - Spanish clinical case reports with PHI annotations.

Provenance: Zenodo record 4279323 (CC BY 4.0), downloaded by `harness.download_public`.
EVIDENCE LABEL: PUBLIC CLINICAL CORPUS - REAL CLINICAL CASE NARRATIVES WITH SYNTHETIC PHI.
The narratives come from published Spanish clinical case reports (SPACCC); PHI values were
inserted manually by health documentalists. It is NOT real patient PHI and must not be
reported as a real-data reproduction.

The official train/dev/test split is preserved: dev is used only for threshold selection,
test only for evaluation; train is never used (nothing is trained).
"""
from __future__ import annotations

from pathlib import Path

from harness.datasets.base import Dataset, sha256_path
from harness.schema import GoldEntity, IgnoreSpan, Sample

RELATION_WORDS = {"madre", "padre", "hija", "hijo", "hijas", "hijos", "hermana", "hermano", "hermanas",
                  "hermanos", "esposa", "esposo", "marido", "mujer", "familia", "familiares", "padres",
                  "abuela", "abuelo", "abuelos", "tía", "tío", "tíos", "prima", "primo", "sobrina",
                  "sobrino", "nieta", "nieto", "pareja", "cuñada", "cuñado", "suegra", "suegro"}
_MAP = {
    "NOMBRE_SUJETO_ASISTENCIA": "NAME", "NOMBRE_PERSONAL_SANITARIO": "PROVIDER_NAME",
    "FECHAS": "DATE", "CALLE": "ADDRESS", "ID_SUJETO_ASISTENCIA": "MRN",
    "ID_TITULACION_PERSONAL_SANITARIO": "NPI", "ID_ASEGURAMIENTO": "MEMBER_ID",
    "ID_CONTACTO_ASISTENCIAL": "OTHER_UNIQUE_ID", "ID_EMPLEO_PERSONAL_SANITARIO": "OTHER_UNIQUE_ID",
    "NUMERO_IDENTIF": "OTHER_UNIQUE_ID", "IDENTIF_VEHICULOS_NRSERIE_PLACAS": "OTHER_UNIQUE_ID",
    "IDENTIF_DISPOSITIVOS_NRSERIE": "OTHER_UNIQUE_ID", "IDENTIF_BIOMETRICOS": "OTHER_UNIQUE_ID",
    "CORREO_ELECTRONICO": "EMAIL", "NUMERO_TELEFONO": "PHONE", "NUMERO_FAX": "PHONE",
    "URL_WEB": "URL", "DIREC_PROT_INTERNET": "URL", "HOSPITAL": "FACILITY_NAME",
    "CENTRO_SALUD": "FACILITY_NAME", "INSTITUCION": "ORGANIZATION",
}
# Not scored as PHI under the default redaction policy -> ignore spans.
_IGNORE = {"EDAD_SUJETO_ASISTENCIA", "SEXO_SUJETO_ASISTENCIA", "PROFESION", "PAIS",
           "OTROS_SUJETO_ASISTENCIA"}
SPLIT_DIRS = {"meddocan_dev": "dev", "meddocan_test": "test", "meddocan_train": "train"}


def _canonical(typ: str, text: str) -> str | None:
    if typ == "TERRITORIO":
        return "ZIP" if text.replace(" ", "").isdigit() else "CITY"
    if typ == "FAMILIARES_SUJETO_ASISTENCIA":
        return None if text.lower().strip() in RELATION_WORDS or not text[:1].isupper() else "RELATIVE_NAME"
    if typ in _IGNORE:
        return None
    return _MAP.get(typ, "OTHER_UNIQUE_ID")


def simulated_registry(ents: list[GoldEntity]) -> dict:
    """ORACLE registry derived from gold (what a hospital EHR would know). Upper bound only."""
    by = lambda lab: sorted({e.text for e in ents if e.label == lab})
    return {"simulated_from_gold": True,
            "member": {"names": by("NAME"), "member_ids": by("MEMBER_ID") + by("MRN"),
                       "addresses": by("ADDRESS"), "emails": [], "phones": []},
            "providers": [{"name": n} for n in by("PROVIDER_NAME")],
            "provider_ids": by("NPI"), "staff": [], "facilities": [{"name": n, "aliases": []} for n in by("FACILITY_NAME")],
            "employers": [], "contacts": by("RELATIVE_NAME")}


def load_meddocan(name: str, root: Path) -> Dataset:
    split = SPLIT_DIRS[name]
    d = root / split / "brat"
    if not d.exists():
        raise FileNotFoundError(f"{d} not found - run `python -m harness.download_public`")
    samples = []
    for ann in sorted(d.glob("*.ann")):
        text = ann.with_suffix(".txt").read_text(encoding="utf-8")
        ents, ign = [], []
        for line in ann.read_text(encoding="utf-8").splitlines():
            if not line.startswith("T"):
                continue
            _, meta, surface = line.split("\t", 2)
            parts = meta.split()
            typ, s, e = parts[0], int(parts[1]), int(parts[-1])
            if text[s:e] != surface:
                continue   # discontinuous / misaligned span: skip rather than guess
            canon = _canonical(typ, surface)
            if canon is None:
                ign.append(IgnoreSpan(start=s, end=e, reason=f"MEDDOCAN {typ} not scored under redaction policy"))
            else:
                ents.append(GoldEntity(start=s, end=e, text=surface, label=canon, difficulty="unknown",
                                       tags=[f"native:{typ}"]))
        samples.append(Sample(id=ann.stem, text=text, entities=ents, ignore_spans=ign, metadata={
            "subset": "MEDDOCAN", "language": "es", "noise_level": "real-text", "source": "public",
            "dataset": name, "split": split, "registry": simulated_registry(ents),
            "evidence_label": "PUBLIC CLINICAL CORPUS (real case narratives, synthetic PHI)"}))
    return Dataset(name, samples, "public", str(d), sha256_path(d), False,
                   ["Real Spanish clinical case-report narratives with SYNTHETIC PHI inserted by documentalists (CC BY 4.0).",
                    "AGE, SEX, PROFESSION, COUNTRY, OTHER-SUBJECT spans and bare relation words are ignore regions.",
                    "Registry for registry-matching stage is an ORACLE simulated from gold (upper bound)."])
