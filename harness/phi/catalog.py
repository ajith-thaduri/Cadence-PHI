"""Entity catalog API: the single place that answers 'what is this entity and how is it treated?'.

Everything that used to be a Python constant (high-risk list, titles / ID affixes / edge words for
scoring, gate groups, Safe Harbor coverage, outbound-guard behaviour, default actions) is read here
from configs/phi/entities.yaml via the resolved HarnessConfig.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from harness.phi.config import HarnessConfig

DEFAULT_TITLES = ["dr", "dra", "doctor", "doctora", "mr", "mrs", "ms", "miss", "sr", "sra", "srta", "nurse", "prof"]
SAFE_HARBOR_18 = ["names", "geographic", "dates", "telephone", "fax", "email", "ssn", "mrn", "health_plan_beneficiary",
                  "account", "certificate_license", "vehicle", "device", "url", "ip_address", "biometric", "photo",
                  "other_unique_id"]            # + age_over_89 is part of the dates element


@dataclass(frozen=True)
class EntityInfo:
    entity_id: str
    enabled: bool
    description: str
    category: str
    risk_tier: str
    hipaa_safe_harbor: tuple[str, ...]
    default_action: Optional[str]
    subtypes: tuple[str, ...]
    titles: tuple[str, ...]
    affixes: tuple[str, ...]
    strip_edge_words: bool
    outbound_guard: str
    high_risk: bool
    gate_group: str


class Catalog:
    def __init__(self, cfg: HarnessConfig) -> None:
        self.cfg = cfg
        self.version = str(cfg.raw.get("catalog_version", "legacy"))
        self._e: dict[str, EntityInfo] = {}
        for eid, spec in cfg.raw["entities"].items():
            cc = spec.get("core_chars") or {}
            st = cc.get("strip_titles", True)
            titles = tuple(DEFAULT_TITLES if st is True else (st or []))
            ev = spec.get("evaluation") or {}
            self._e[eid] = EntityInfo(eid, bool(spec.get("enabled", True)), spec.get("description", ""),
                                      spec.get("category", "other"), spec.get("risk_tier", "contextual"),
                                      tuple(spec.get("hipaa_safe_harbor") or []), spec.get("default_action"),
                                      tuple(spec.get("subtypes") or []), titles, tuple(cc.get("strip_affixes") or []),
                                      bool(cc.get("strip_edge_words", True)), spec.get("outbound_guard", "off"),
                                      bool(ev.get("high_risk", False)), ev.get("gate_group", "none"))

    def __contains__(self, eid: str) -> bool:
        return eid in self._e

    def __getitem__(self, eid: str) -> EntityInfo:
        return self._e[eid]

    def get(self, eid: str) -> Optional[EntityInfo]:
        return self._e.get(eid)

    @property
    def ids(self) -> list[str]:
        return list(self._e)

    def high_risk(self) -> list[str]:
        return [e for e, i in self._e.items() if i.high_risk]

    def by_tier(self, tier: str) -> list[str]:
        return [e for e, i in self._e.items() if i.risk_tier == tier]

    def by_gate_group(self, group: str) -> list[str]:
        return [e for e, i in self._e.items() if i.gate_group == group]

    def outbound_block(self) -> list[str]:
        return [e for e, i in self._e.items() if i.enabled and i.outbound_guard == "block"]

    def subtype_parent(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for e, i in self._e.items():
            for s in i.subtypes:
                out.setdefault(s, e)
        return out

    def safe_harbor_coverage(self) -> dict[str, dict[str, Any]]:
        """Safe Harbor identifier -> catalog entities covering it and whether any is enabled."""
        out: dict[str, dict[str, Any]] = {}
        for sh in SAFE_HARBOR_18 + ["age_over_89"]:
            ents = [e for e, i in self._e.items() if sh in i.hipaa_safe_harbor]
            out[sh] = {"entities": ents, "enabled": [e for e in ents if self._e[e].enabled],
                       "status": ("covered" if any(self._e[e].enabled for e in ents) else
                                  "defined_disabled" if ents else "not_applicable_to_text"
                                  if sh in ("biometric", "photo") else "not_defined")}
        return out
