"""Engine configuration: YAML + per-request / CLI overrides, threshold resolution.

The config only DESCRIBES what is enabled and which thresholds apply; it never contains logic.
Request-level allow-lists (DetectionRequest.enabled_*) can only narrow what the config enables.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from harness.config import load_yaml
from harness.paths import ROOT

DEFAULT_ROOT = "configs/phi/phi.yaml"          # layered configuration (composition root)
LEGACY_FILE = "configs/phi_harness.yaml"       # legacy single-file configuration (still loadable)


class ConfigError(ValueError):
    pass


class UnsupportedLanguageError(ValueError):
    """The request language is outside the configured scope (this phase: English only)."""


SECTIONS = ("entities", "thresholds", "recognizers", "models", "normalizers", "normalization_profiles",
            "policy_profiles", "precedence")


@dataclass
class HarnessConfig:
    raw: dict[str, Any]
    path: str = "configs/phi_harness.yaml"

    # ---- construction ----------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str] = None, env: Optional[str] = None,
             apply_generated: Optional[bool] = None) -> "HarnessConfig":
        """Load + validate. `path` may be a composition root (layered config) or a legacy single file.
        Default: configs/phi/phi.yaml when present, else configs/phi_harness.yaml."""
        from harness.phi.config.loader import LoaderError, compose
        if path is None:
            path = DEFAULT_ROOT if (ROOT / DEFAULT_ROOT).exists() else LEGACY_FILE
        full = path if (path.startswith("/") or not (ROOT / path).exists()) else str(ROOT / path)
        head = load_yaml(full) or {}
        try:
            raw = compose(full, env, apply_generated) if "includes" in head else head
        except LoaderError as exc:
            raise ConfigError(str(exc)) from None
        cfg = cls(raw, path)
        for spec in (cfg.raw.get("recognizers") or {}).values():
            spec.setdefault("enabled_default", bool(spec.get("enabled", False)))   # presets never enable an opt-in recognizer
        cfg.raw.setdefault("_overlays", [])
        cfg.validate()
        return cfg

    def copy(self) -> "HarnessConfig":
        return HarnessConfig(copy.deepcopy(self.raw), self.path)

    def sha256(self) -> str:
        """Hash of the RESOLVED configuration (all files, overlays and overrides applied)."""
        return hashlib.sha256(json.dumps({k: v for k, v in self.raw.items() if not k.startswith("_")},
                                         sort_keys=True, default=str).encode()).hexdigest()[:16]

    def validate(self) -> None:
        """Strict schema + reference integrity (harness/phi/config/schema.py). Fails closed."""
        from pydantic import ValidationError

        from harness.phi.config.schema import ResolvedConfig
        missing = [s for s in SECTIONS if s not in self.raw]
        if missing:
            raise ConfigError(f"config missing sections: {missing}")
        try:
            ResolvedConfig.model_validate({k: v for k, v in self.raw.items() if not k.startswith("_")})
        except ValidationError as exc:
            msgs = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:12])
            raise ConfigError(f"invalid configuration: {msgs}") from None
        from harness.config import model_entry
        for m in self.raw["models"]:
            try:
                model_entry(m)
            except Exception:  # noqa: BLE001
                raise ConfigError(f"model {m!r} is not registered in configs/models.yaml") from None

    @property
    def entity_ids(self) -> list[str]:
        return list(self.raw["entities"])

    # ---- entity selection ------------------------------------------------------------------
    def expand(self, names: Iterable[str]) -> list[str]:
        out: list[str] = []
        aliases = self.raw.get("aliases", {})
        for n in names:
            n = n.strip().upper()
            if not n:
                continue
            if n in aliases:
                out += aliases[n]
            elif n in self.raw["entities"]:
                out.append(n)
            else:
                raise ConfigError(f"unknown entity or alias {n!r}; known aliases: {sorted(aliases)}")
        return list(dict.fromkeys(out))

    def only_entities(self, names: Iterable[str]) -> None:
        keep = set(self.expand(names))
        for e, spec in self.raw["entities"].items():
            spec["enabled"] = e in keep

    def set_entities(self, names: Iterable[str], enabled: bool) -> None:
        for e in self.expand(names):
            self.raw["entities"].setdefault(e, {})["enabled"] = enabled

    @property
    def enabled_entities(self) -> list[str]:
        return [e for e, s in self.raw["entities"].items() if s.get("enabled", True)]

    # ---- models / recognizers / normalizers -------------------------------------------------
    @property
    def enabled_models(self) -> list[str]:
        return [m for m, s in self.raw["models"].items() if s.get("enabled")]

    def only_models(self, models: Iterable[str]) -> None:
        ms = [m.strip() for m in models if m.strip()]
        for m in ms:
            if m not in self.raw["models"]:
                raise ConfigError(f"unknown model {m!r}; known: {sorted(self.raw['models'])}")
        for m, s in self.raw["models"].items():
            s["enabled"] = m in ms

    @property
    def shadow_ids(self) -> set[str]:
        """Recognizer / model ids running in shadow mode (evidence recorded, never used for decisions)."""
        out = {r for r, s in self.raw["recognizers"].items() if s.get("mode") == "shadow"}
        return out | {f"model_{k}" for k, s in self.raw["models"].items() if s.get("mode") == "shadow"}

    def recognizer_enabled(self, name: str) -> bool:
        return bool(self.raw["recognizers"].get(name, {}).get("enabled", False))

    def set_recognizers(self, names: Iterable[str], enabled: bool) -> None:
        for n in names:
            n = n.strip()
            if n not in self.raw["recognizers"]:
                raise ConfigError(f"unknown recognizer {n!r}")
            self.raw["recognizers"][n]["enabled"] = enabled

    def normalizer_enabled(self, name: str) -> bool:
        return bool((self.raw["normalizers"].get(name) or {}).get("enabled", False))

    def normalizers_for(self, profile: str) -> list[str]:
        if profile not in self.raw["normalization_profiles"]:
            raise ConfigError(f"unknown normalization_profile {profile!r}; known: {sorted(self.raw['normalization_profiles'])}")
        return [n for n in self.raw["normalization_profiles"][profile] if self.normalizer_enabled(n)]

    def policy(self, profile: str) -> dict[str, Any]:
        if profile not in self.raw["policy_profiles"]:
            raise ConfigError(f"unknown policy_profile {profile!r}; known: {sorted(self.raw['policy_profiles'])}")
        return self.raw["policy_profiles"][profile]

    @property
    def supported_languages(self) -> list[str]:
        return list((self.raw.get("languages") or {}).get("supported") or ["en"])

    @property
    def recognizer_languages(self) -> list[str]:
        """Presidio registry languages actually built (values of languages.map)."""
        return sorted(set(((self.raw.get("languages") or {}).get("map") or {"en": "en"}).values()))

    def resolve_language(self, lang: str) -> str:
        if lang not in self.supported_languages:
            raise UnsupportedLanguageError(
                f"language {lang!r} is not supported by this profile (supported: {self.supported_languages})")
        return (self.raw.get("languages", {}).get("map") or {}).get(lang, "en")

    # ---- presets / overrides ----------------------------------------------------------------
    def apply_preset(self, preset: dict[str, Any] | str) -> None:
        """Preset keys: models (list), rules / contextual / registry / normalization (bool), set (dict)."""
        if isinstance(preset, str):
            presets = self.presets
            if preset not in presets:
                raise ConfigError(f"unknown preset {preset!r}; known: {sorted(presets)}")
            preset = presets[preset]
        if "models" in preset:
            self.only_models(preset["models"])
        for n, spec in self.raw["recognizers"].items():
            group = ("contextual" if spec.get("contextual") else "registry" if "kinds" in spec
                     else "rules" if "family" in spec else None)
            if group and group in preset:
                # contextual recognizers are opt-in by default; a preset may switch them on explicitly
                spec["enabled"] = bool(preset[group]) and (spec.get("enabled_default", True) or group == "contextual")
        if "normalization" in preset:
            for spec in self.raw["normalizers"].values():
                spec["enabled"] = bool(preset["normalization"])
        for k, v in (preset.get("set") or {}).items():
            self.set_value(k, v)

    apply_profile = apply_preset     # backwards-compatible name

    @property
    def presets(self) -> dict[str, Any]:
        """Ablation presets: evaluation configuration (configs/eval/presets.yaml), or a legacy in-file block."""
        if self.raw.get("presets"):
            return self.raw["presets"]
        p = ROOT / "configs" / "eval" / "presets.yaml"
        return (load_yaml(str(p)) or {}).get("presets", {}) if p.exists() else {}

    def set_value(self, dotted: str, value: Any) -> None:
        """--set thresholds.model_entity.obi_roberta.NAME=0.3"""
        cur = self.raw
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        if isinstance(value, str):
            low = value.lower()
            if low in ("true", "false"):
                value = low == "true"
            else:
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass
        cur[parts[-1]] = value

    # ---- thresholds -------------------------------------------------------------------------
    def threshold_for(self, entity: str, recognizer: str, model: Optional[str], score_type: str,
                      modality: Optional[str] = None) -> tuple[float, str]:
        """(threshold, config key that supplied it). Most specific wins:
        model_confidence: modality.model_entity > model_entity > modality.entity > entity > model > recognizer > default
        deterministic / registry: recognizer > deterministic_default."""
        t = self.raw["thresholds"]
        rec = (t.get("recognizer") or {}).get(recognizer)
        mod = ((t.get("modality") or {}).get(str(modality)) or {}) if modality else {}
        if score_type in (t.get("applies_to_score_types") or ["model_confidence"]):
            chain = [(((mod.get("model_entity") or {}).get(model or "") or {}).get(entity),
                      f"thresholds.modality.{modality}.model_entity.{model}.{entity}"),
                     (((t.get("model_entity") or {}).get(model or "") or {}).get(entity), f"thresholds.model_entity.{model}.{entity}"),
                     ((mod.get("entity") or {}).get(entity), f"thresholds.modality.{modality}.entity.{entity}"),
                     ((t.get("entity") or {}).get(entity), f"thresholds.entity.{entity}"),
                     ((t.get("model") or {}).get(model or ""), f"thresholds.model.{model}"),
                     (rec, f"thresholds.recognizer.{recognizer}"),
                     (t.get("default", 0.5), "thresholds.default")]
        else:
            chain = [(rec, f"thresholds.recognizer.{recognizer}"),
                     (t.get("deterministic_default", 0.0), "thresholds.deterministic_default")]
        for val, src in chain:
            if val is not None:
                return float(val), src
        return 0.5, "builtin"

    def describe(self) -> dict[str, Any]:
        return {"config_sha256": self.sha256(), "environment": self.raw.get("environment", "legacy"),
                "catalog_version": self.raw.get("catalog_version", "legacy"),
                "sources": (self.raw.get("_source") or {}).get("files", [self.path]),
                "entities_enabled": self.enabled_entities,
                "models_enabled": self.enabled_models,
                "recognizers_enabled": [r for r in self.raw["recognizers"] if self.recognizer_enabled(r)],
                "normalizers_enabled": [n for n in self.raw["normalizers"] if self.normalizer_enabled(n)],
                "threshold_overlays": self.raw.get("_overlays", []),
                "thresholds_note": "model_confidence: model_entity > entity > model > recognizer > default; "
                                   "deterministic/registry: recognizer > deterministic_default"}
