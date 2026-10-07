"""Local-path adapters for the n2c2/i2b2 de-identification corpora.

These datasets require a signed Data Use Agreement (DBMI Data Portal). The harness
NEVER downloads them; point --data-path at your own licensed copy.

i2b2 2014  directory of XML files:  <deIdi2b2><TEXT><![CDATA[...]]></TEXT>
           <TAGS><NAME id=.. start=.. end=.. text=.. TYPE="PATIENT"/> ...</TAGS></deIdi2b2>
i2b2 2006  single XML file:  <ROOT><RECORD ID=".."><TEXT> ... <PHI TYPE="DOCTOR">x</PHI> ...
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from harness.datasets.base import Dataset, sha256_path
from harness.schema import GoldEntity, IgnoreSpan, Sample

# i2b2 TYPE -> canonical. None = no canonical category -> ignore span (neither TP nor FP).
I2B2_2014_MAP: dict[str, str | None] = {
    "PATIENT": "NAME", "DOCTOR": "PROVIDER_NAME", "USERNAME": "OTHER_UNIQUE_ID",
    "HOSPITAL": "FACILITY_NAME", "ORGANIZATION": "ORGANIZATION", "STREET": "ADDRESS",
    "CITY": "CITY", "STATE": "STATE", "ZIP": "ZIP", "COUNTRY": "GEOGRAPHIC_CONTEXT",
    "LOCATION-OTHER": "GEOGRAPHIC_CONTEXT", "DATE": "DATE", "PHONE": "PHONE", "FAX": "PHONE",
    "EMAIL": "EMAIL", "URL": "URL", "MEDICALRECORD": "MRN", "HEALTHPLAN": "MEMBER_ID",
    "ACCOUNT": "ACCOUNT_ID", "LICENSE": "OTHER_UNIQUE_ID", "DEVICE": "OTHER_UNIQUE_ID",
    "BIOID": "OTHER_UNIQUE_ID", "IDNUM": "OTHER_UNIQUE_ID", "AGE": None, "PROFESSION": None,
}
I2B2_2006_MAP: dict[str, str | None] = {
    "PATIENT": "NAME", "DOCTOR": "PROVIDER_NAME", "HOSPITAL": "FACILITY_NAME",
    "LOCATION": "ADDRESS", "DATE": "DATE", "ID": "OTHER_UNIQUE_ID", "PHONE": "PHONE", "AGE": None,
}


def _sample(sid: str, text: str, spans: list[tuple[int, int, str]], mapping, ds: str) -> Sample:
    ents, ign = [], []
    for s, e, typ in spans:
        canon = mapping.get(typ.upper())
        if canon is None:
            ign.append(IgnoreSpan(start=s, end=e, reason=f"{ds} type {typ} has no canonical label"))
        else:
            ents.append(GoldEntity(start=s, end=e, text=text[s:e], label=canon,
                                   difficulty="unknown", tags=[f"native:{typ}"]))
    return Sample(id=sid, text=text, entities=ents, ignore_spans=ign,
                  metadata={"subset": ds.upper(), "language": "en", "noise_level": "real",
                            "source": "public", "dataset": ds})


def load_i2b2_2014(path: Path) -> Dataset:
    files = sorted(path.rglob("*.xml")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"no .xml files under {path}")
    samples = []
    for f in files:
        root = ET.parse(f).getroot()
        text = root.findtext("TEXT") or ""
        spans = []
        tags = root.find("TAGS")
        for t in (tags if tags is not None else []):
            s, e = int(t.attrib["start"]), int(t.attrib["end"])
            spans.append((s, e, t.attrib.get("TYPE", t.tag)))
        samples.append(_sample(f.stem, text, spans, I2B2_2014_MAP, "i2b2_2014"))
    return Dataset("i2b2_2014", samples, "public", str(path), sha256_path(path), False,
                   ["AGE and PROFESSION spans are ignore regions (no canonical label)."])


def load_i2b2_2006(path: Path) -> Dataset:
    root = ET.parse(path).getroot()
    samples = []
    for rec in root.iter("RECORD"):
        node = rec.find("TEXT")
        parts: list[str] = [node.text or ""] if node is not None else []
        spans = []
        pos = len(parts[0]) if parts else 0
        for child in (node if node is not None else []):
            val = child.text or ""
            if child.tag == "PHI":
                spans.append((pos, pos + len(val), child.attrib.get("TYPE", "UNKNOWN")))
            parts.append(val)
            pos += len(val)
            parts.append(child.tail or "")
            pos += len(child.tail or "")
        samples.append(_sample(rec.attrib.get("ID", str(len(samples))), "".join(parts), spans,
                               I2B2_2006_MAP, "i2b2_2006"))
    return Dataset("i2b2_2006", samples, "public", str(path), sha256_path(path), False,
                   ["LOCATION mapped to ADDRESS; AGE spans are ignore regions."])
